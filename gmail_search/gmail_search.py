#!/usr/bin/env python3
"""
Gmail Search CLI with smart caching and result management.
Features:
- Smart email caching with date range tracking
- Local search on cached data
- Efficient IMAP fetching
"""

import email
import imaplib
import os
import re
import sqlite3
import threading
import urllib.parse
from dataclasses import dataclass
from queue import Queue
from typing import List, Optional, Tuple

import pendulum
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

console = Console()


@dataclass
class DateRange:
    """Represents a range of dates."""

    start: pendulum.DateTime
    end: pendulum.DateTime

    def overlaps(self, other: "DateRange") -> bool:
        """Check if this date range overlaps with another."""
        return self.start <= other.end and self.end >= other.start

    def merge(self, other: "DateRange") -> "DateRange":
        """Merge two overlapping date ranges."""
        if not self.overlaps(other):
            raise ValueError("Cannot merge non-overlapping ranges")
        return DateRange(
            start=min(self.start, other.start), end=max(self.end, other.end)
        )


@dataclass
class EmailMetadata:
    """Represents cached email metadata."""

    message_id: str
    from_address: str
    subject: str
    date: pendulum.DateTime
    gmail_link: str


class EmailCache:
    """Manages the local cache of email metadata with date range tracking."""

    def __init__(self, db_path: str = "email_cache.db"):
        self.db_path = db_path
        self._init_db()
        self._lock = threading.Lock()

    def _init_db(self):
        """Initialize the SQLite database schema."""
        with sqlite3.connect(self.db_path) as conn:
            # Enable foreign keys and WAL mode for better concurrency
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA journal_mode = WAL")

            # Email metadata table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS emails (
                    message_id TEXT PRIMARY KEY,
                    from_address TEXT NOT NULL,
                    subject TEXT,
                    date TEXT NOT NULL,
                    gmail_link TEXT,
                    cached_at TEXT NOT NULL
                )
            """
            )

            # Date range coverage table
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS date_ranges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL
                )
            """
            )

            # Indexes for better query performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_date ON emails(date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_from ON emails(from_address)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_cached_at ON emails(cached_at)"
            )

            # Ensure changes are committed
            conn.commit()

    def get_cached_ranges(self) -> List[DateRange]:
        """Get list of date ranges that are fully cached."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT start_date, end_date FROM date_ranges ORDER BY start_date"
            )
            return [
                DateRange(start=pendulum.parse(row[0]), end=pendulum.parse(row[1]))
                for row in cursor.fetchall()
            ]

    def find_missing_ranges(self, target_range: DateRange) -> List[DateRange]:
        """Find date ranges that need to be fetched to cover the target range."""
        cached_ranges = self.get_cached_ranges()
        if not cached_ranges:
            return [target_range]

        missing_ranges = []
        current = target_range.start

        for cached_range in cached_ranges:
            if current < cached_range.start:
                missing_ranges.append(DateRange(current, cached_range.start))
            current = max(current, cached_range.end)

        if current < target_range.end:
            missing_ranges.append(DateRange(current, target_range.end))

        return missing_ranges

    def store_emails(self, emails: List[EmailMetadata], date_range: DateRange):
        """Store email metadata and update cached date ranges."""
        if not emails:
            return

        with self._lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    # Begin transaction
                    conn.execute("BEGIN TRANSACTION")

                    try:
                        # Store email metadata
                        now = pendulum.now("UTC").isoformat()
                        conn.executemany(
                            """
                            INSERT OR REPLACE INTO emails
                            (message_id, from_address, subject, date, gmail_link, cached_at)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            [
                                (
                                    email.message_id,
                                    email.from_address,
                                    email.subject,
                                    email.date.isoformat(),
                                    email.gmail_link,
                                    now,
                                )
                                for email in emails
                            ],
                        )

                        # Update date range coverage
                        self._update_date_ranges(conn, date_range)

                        # Commit transaction
                        conn.commit()
                    except Exception as e:
                        conn.rollback()
                        raise
            except Exception as e:
                raise

    def _update_date_ranges(self, conn: sqlite3.Connection, new_range: DateRange):
        """Update the cached date ranges, merging overlapping ranges."""
        try:
            ranges = self.get_cached_ranges()
            ranges.append(new_range)

            # Sort ranges by start date
            ranges.sort(key=lambda r: r.start)

            # Merge overlapping ranges
            merged = []
            if ranges:
                current = ranges[0]
                for next_range in ranges[1:]:
                    if current.overlaps(next_range):
                        current = current.merge(next_range)
                    else:
                        merged.append(current)
                        current = next_range
                merged.append(current)

            # Update database
            conn.execute("DELETE FROM date_ranges")
            conn.executemany(
                "INSERT INTO date_ranges (start_date, end_date) VALUES (?, ?)",
                [(r.start.isoformat(), r.end.isoformat()) for r in merged],
            )
        except Exception as e:
            raise

    def search_emails(
        self, pattern: str, date_range: Optional[DateRange] = None
    ) -> List[EmailMetadata]:
        """Search cached emails using regex pattern and optional date range."""
        query = "SELECT * FROM emails WHERE from_address REGEXP ?"
        params = [pattern]

        if date_range:
            query += " AND date >= ? AND date <= ?"
            params.extend([date_range.start.isoformat(), date_range.end.isoformat()])

        with sqlite3.connect(self.db_path) as conn:
            conn.create_function(
                "REGEXP", 2, lambda pattern, text: bool(re.search(pattern, text or ""))
            )

            cursor = conn.execute(query, params)
            return [
                EmailMetadata(
                    message_id=row[0],
                    from_address=row[1],
                    subject=row[2],
                    date=pendulum.parse(row[3]),
                    gmail_link=row[4],
                )
                for row in cursor.fetchall()
            ]

    def cleanup_old_data(self, days_to_keep: int = 365):
        """Remove emails older than specified days."""
        cutoff_date = pendulum.now("UTC").subtract(days=days_to_keep)

        with self._lock:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "DELETE FROM emails WHERE date < ?", (cutoff_date.isoformat(),)
                )

                # Update date ranges
                conn.execute(
                    """
                    UPDATE date_ranges
                    SET start_date = ?
                    WHERE start_date < ?
                    """,
                    (cutoff_date.isoformat(), cutoff_date.isoformat()),
                )

                # Remove completely outdated ranges
                conn.execute(
                    "DELETE FROM date_ranges WHERE end_date < ?",
                    (cutoff_date.isoformat(),),
                )


class IMAPClient:
    """Handles all IMAP operations with connection pooling."""

    def __init__(self, email_address: str, app_password: str, max_workers: int = 5):
        self.email_address = email_address
        self.app_password = app_password
        self.max_workers = max_workers
        self.connection_pool = Queue()
        self._init_pool()

    def _init_pool(self):
        """Initialize the connection pool."""
        for _ in range(self.max_workers):
            conn = self._create_connection()
            if conn:
                self.connection_pool.put(conn)

    def _create_connection(self) -> Optional[imaplib.IMAP4_SSL]:
        """Create a new IMAP connection."""
        try:
            imap = imaplib.IMAP4_SSL("imap.gmail.com")
            imap.login(self.email_address, self.app_password)
            imap.select("INBOX", readonly=True)
            return imap
        except Exception:
            return None

    def _get_connection(self) -> Optional[imaplib.IMAP4_SSL]:
        """Get a connection from the pool."""
        try:
            return self.connection_pool.get(timeout=5)
        except Exception:
            return self._create_connection()

    def _release_connection(self, conn: imaplib.IMAP4_SSL):
        """Return a connection to the pool."""
        try:
            self.connection_pool.put(conn, timeout=5)
        except Exception:
            try:
                conn.logout()
            except:
                pass

    def _generate_gmail_link(self, subject: str) -> Optional[str]:
        """Generate Gmail web link for email using subject search."""
        try:
            if not subject:
                return None
            # URL encode the subject for search
            encoded_subject = urllib.parse.quote(subject)
            # Construct the Gmail search URL
            return (
                f"https://mail.google.com/mail/u/0/#search/subject%3A{encoded_subject}"
            )
        except Exception as e:
            print(f"Error generating Gmail link: {str(e)}")
            return None

    def fetch_emails(self, date_range: DateRange) -> List[EmailMetadata]:
        """Fetch emails from IMAP server for a given date range."""
        conn = self._get_connection()
        if not conn:
            raise RuntimeError("Could not establish IMAP connection")

        try:
            # Construct IMAP search criteria
            search_criteria = [
                "SINCE",
                date_range.start.format("D-MMM-YYYY"),
                "BEFORE",
                date_range.end.add(days=1).format("D-MMM-YYYY"),
            ]

            # Search for messages in the date range
            _, messages = conn.search(None, *search_criteria)
            message_numbers = messages[0].split()

            if not message_numbers:
                return []

            emails = []
            batch_size = 100  # Process in batches to avoid memory issues
            total_messages = len(message_numbers)

            # Show progress for email fetching
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                console=console,
            ) as progress:
                overall_task = progress.add_task(
                    f"[cyan]Fetching {total_messages} emails from {date_range.start.format('YYYY-MM-DD')} to {date_range.end.format('YYYY-MM-DD')}...",
                    total=total_messages,
                )

                for i in range(0, total_messages, batch_size):
                    current_batch_size = min(batch_size, total_messages - i)
                    batch = message_numbers[i : i + current_batch_size]
                    message_set = b",".join(batch).decode("utf-8")

                    # Fetch headers for the batch
                    _, header_data = conn.fetch(
                        message_set,
                        "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])",
                    )

                    # Process each message in the batch
                    for j in range(0, len(header_data), 2):
                        if not header_data[j]:
                            progress.advance(overall_task)
                            continue

                        header = email.message_from_bytes(header_data[j][1])

                        # Parse email metadata
                        message_id = header.get("Message-ID", "").strip()
                        from_address = self._decode_header(header.get("From", ""))
                        subject = self._decode_header(
                            header.get("Subject", "(No Subject)")
                        )
                        date = self._parse_date(header.get("Date"))

                        # Generate Gmail link using subject
                        gmail_link = (
                            self._generate_gmail_link(f'"{subject}"')
                            if subject
                            else None
                        )

                        if all([message_id, from_address, date]):
                            emails.append(
                                EmailMetadata(
                                    message_id=message_id,
                                    from_address=from_address,
                                    subject=subject,
                                    date=date,
                                    gmail_link=gmail_link,
                                )
                            )

                        # Update progress
                        progress.advance(overall_task)
            return emails

        finally:
            self._release_connection(conn)

    def _decode_header(self, header: str) -> str:
        """Safely decode email headers."""
        if not header:
            return ""
        try:
            # Convert Header object to string if necessary
            if not isinstance(header, str):
                header = str(header)

            decoded = email.header.decode_header(header)[0]
            value, charset = decoded
            if isinstance(value, bytes):
                return value.decode(charset or "utf-8", errors="replace")
            return str(value)
        except:
            # If any error occurs during decoding, return the string representation
            return str(header)

    def _parse_date(self, date_str: str) -> pendulum.DateTime:
        """Parse email date string to pendulum DateTime."""
        if not date_str:
            return pendulum.now("UTC")
        try:
            dt = email.utils.parsedate_to_datetime(date_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pendulum.UTC)
            return pendulum.instance(dt)
        except:
            return pendulum.now("UTC")

    def cleanup(self):
        """Clean up all IMAP connections."""
        while not self.connection_pool.empty():
            try:
                conn = self.connection_pool.get_nowait()
                try:
                    conn.close()
                    conn.logout()
                except:
                    pass
            except:
                break


class EmailSearchService:
    """High-level service that orchestrates email searching and caching."""

    def __init__(self, email_address: str, app_password: str):
        self.cache = EmailCache()
        self.imap_client = IMAPClient(email_address, app_password)

    def search(
        self, pattern: str, days_back: Optional[int] = None
    ) -> List[EmailMetadata]:
        """
        Search for emails matching the pattern.
        If days_back is specified, only search emails from the last N days.
        """
        # Calculate target date range
        end_date = pendulum.now("UTC")
        start_date = (
            end_date.subtract(days=days_back)
            if days_back
            else end_date.subtract(years=1)
        )
        target_range = DateRange(start=start_date, end=end_date)

        # Find what date ranges we're missing from cache
        missing_ranges = self.cache.find_missing_ranges(target_range)

        # First, handle any missing date ranges
        if missing_ranges:
            # Calculate total days to fetch (excluding recent buffer)
            fetch_ranges = [r for r in missing_ranges]

            if fetch_ranges:
                # Calculate total days including both start and end dates
                total_days = sum(
                    (r.end.diff(r.start).in_days() + 1) for r in fetch_ranges
                )
                # Fetch and store emails for each date range
                for date_range in fetch_ranges:
                    try:
                        emails = self.imap_client.fetch_emails(date_range)
                        if emails:
                            self.cache.store_emails(emails, date_range)
                    except Exception as e:
                        console.print(f"\n[red]Error fetching emails: {str(e)}[/red]")

        # Now search through the cache
        results = self.cache.search_emails(pattern, target_range)

        # Sort by date, newest first
        results = sorted(results, key=lambda x: x.date, reverse=True)

        console.print(f"[green]✓ Found {len(results)} matching emails[/green]")

        return results

    def cleanup(self):
        """Clean up resources."""
        try:
            self.imap_client.cleanup()
        except Exception:
            pass


def validate_credentials(email: str, password: str) -> bool:
    """Validate Gmail credentials."""
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com")
        imap.login(email, password)
        imap.logout()
        return True
    except Exception:
        return False


def setup_credentials() -> Tuple[str, str]:
    """Set up Gmail credentials from environment or user input."""
    load_dotenv()
    email = os.getenv("GMAIL_ADDRESS")
    password = os.getenv("GMAIL_APP_PASSWORD")

    if email and password and validate_credentials(email, password):
        return email, password

    console.print("\n[bold]Gmail Account Setup[/bold]")
    console.print(
        """
    1. Enable 2-Step Verification in your Google Account
    2. Generate an App Password for Gmail
    """
    )

    email = input("Enter your Gmail address: ")
    password = input("Enter your App Password: ")

    if validate_credentials(email, password):
        # Save credentials
        with open(".env", "w") as f:
            f.write(f"GMAIL_ADDRESS={email}\n")
            f.write(f"GMAIL_APP_PASSWORD={password}\n")
        return email, password

    raise ValueError("Invalid credentials")


def show_title():
    """Display the application title and description."""
    console.clear()
    console.print(
        """[bold blue]
╔═══════════════════════════════════════════╗
║             Gmail Search CLI              ║
╚═══════════════════════════════════════════╝[/bold blue]
"""
    )
    console.print(
        """[cyan]A powerful tool for searching your Gmail inbox with:
• Smart local caching for fast repeated searches
• Regex pattern matching for precise searching
"""
    )


def get_search_parameters() -> Tuple[str | None, Optional[int]]:
    """Get search pattern and date range from user."""
    while True:
        pattern = console.input(
            "\n[bold white]Enter search pattern (regex supported) or 'exit' to quit:[/bold white] "
        )
        if pattern.lower() == "exit":
            return None, None

        try:
            re.compile(pattern)
            break
        except re.error:
            console.print("[red]Invalid regex pattern. Please try again.[/red]")

    days_input = console.input(
        "\n[bold white]Search emails from last N days (Enter for last year):[/bold white] "
    ).strip()

    try:
        days = int(days_input) if days_input else None
        if days is not None and days < 1:
            raise ValueError("Days must be positive")
        return pattern, days
    except ValueError:
        console.print("[yellow]Invalid day value, defaulting to all emails[/yellow]")
        return pattern, None


def display_search_results(results: List[EmailMetadata], pattern: str):
    """Display search results in a formatted table."""
    if not results:
        console.print("\n[yellow]No matching emails found.[/yellow]")
        return False

    # Create a table for better display
    table = Table(
        show_header=True,
        header_style="bold magenta",
        border_style="blue",
        title=f"Search Results ({pattern})",
    )
    table.add_column("Date", style="cyan", width=20)
    table.add_column("From", style="yellow", width=30)
    table.add_column("Subject", style="white", width=50)
    table.add_column("Link", style="blue", width=30)

    for email in results:
        table.add_row(
            email.date.format("YYYY-MM-DD HH:mm"),
            email.from_address,
            email.subject,
            f"[link={email.gmail_link}]🔗 Open in Gmail[/link]",
        )

    console.print(table)
    return True


def handle_search(service: EmailSearchService):
    """Handle the email search workflow."""
    try:
        pattern, days = get_search_parameters()
        if pattern is None:  # User wants to exit
            return False

        results = service.search(pattern, days)
        display_search_results(results, pattern)
        console.print("\n[cyan]Press Enter to search again...[/cyan]")
        input()
        return True
    except ValueError as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        input("\nPress Enter to continue...")
        return True
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
        input("\nPress Enter to continue...")
        return True


def initialize_service() -> EmailSearchService:
    """Initialize the email search service with proper user feedback."""
    with console.status("[bold blue]Setting up Gmail connection...") as status:
        try:
            email, password = setup_credentials()
            status.update("[bold blue]Initializing search service...")
            service = EmailSearchService(email, password)
            console.print("[green]✓ Successfully connected to Gmail![/green]")
            return service
        except Exception as e:
            console.print(f"[red]Error setting up Gmail connection: {str(e)}[/red]")
            raise


def main():
    """Main entry point for the Gmail search tool."""
    try:
        show_title()
        service = initialize_service()

        while True:
            show_title()
            if not handle_search(service):
                console.print("\n[green]Thank you for using Gmail Search CLI![/green]")
                break

    except KeyboardInterrupt:
        console.print("\n[yellow]Program interrupted by user[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {str(e)}[/red]")
    finally:
        if "service" in locals():
            service.cleanup()


if __name__ == "__main__":
    main()
