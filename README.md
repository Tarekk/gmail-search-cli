# 🚀 Gmail Search CLI

> Because life's too short for Gmail's search limitations!

Ever tried searching for "book" in Gmail and missed emails from "canadabooks@ca"? Yeah, me too. That's why this CLI tool exists - it's like grep for your Gmail, but cooler!

## 🌟 Features

  - **Smart Caching**: Lightning-fast repeated searches because nobody likes waiting
  - **Regex Power**: Find exactly what you want with the power of regular expressions
  - **Local First**: Search through your cached emails even when offline
  - **Progress Tracking**: Pretty progress bars because we all love those
  - **Gmail Web Links**: Direct links to emails in your Gmail web interface

## 💾 Data Storage

All email metadata is stored locally in a SQLite database (`email_cache.db`) in your repository folder. This includes:

  - Email subjects
  - Sender addresses
  - Dates
  - Message IDs
  - Gmail web links

No email bodies or attachments are stored - just the metadata needed for searching. You can safely delete the database at any time; it will be rebuilt on your next search.

## 🎨 Fun Fact

99% of this code was written by an AI! The programming language? English. Yes, you read that right - this entire project was primarily generated through LLM prompts. Even this README you're reading right now is AI-generated. We're going full meta here! Welcome to the future of coding! 🤖✨

## 🔧 Requirements

  - Python 3.12 or higher
  - pipx ([installation instructions](https://pipx.pypa.io/stable/installation/))
  - Rust and Cargo (required for Pendulum library)
  - A Gmail account with 2FA enabled
  - Your sanity (searching emails can be frustrating!)

## 🚀 Installation

1. Install Rust and Cargo (required for Pendulum):

   ```bash
   curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
   ```

2. Clone this repository:

   ```bash
   git clone https://github.com/Tarekk/gmail-search-cli
   cd gmail-search-cli
   ```

3. Copy the environment file:

   ```bash
   cp .env.example .env
   ```

4. Set up your credentials:

   - Enable 2-Step Verification in your Google Account
   - Generate an App Password: [Google Account Settings](https://support.google.com/mail/answer/185833?hl=en)
   - Add your credentials to `.env`:
     ```
     GMAIL_ADDRESS=your.email@gmail.com
     GMAIL_APP_PASSWORD=your-app-password
     ```

5. Install globally with pipx:
   ```bash
   pipx install -e .
   ```

## ⚡ Usage

Simply run:

```bash
gmail-search
```

The CLI will guide you through:

1. Setting up your credentials (first time only)
2. Entering your search pattern (regex supported!)
3. Specifying how far back to search

## ⚠️ Security Notice

This tool requires a Gmail App Password. Please:

  - Keep your App Password secure
  - Never share it or commit it to version control
  - Use this tool at your own risk
  - Consider revoking the App Password when you're done using the tool

## 🔍 Why This Exists

Gmail's search can sometimes miss emails that should match your query. This tool provides:

  - Regex pattern matching for more precise searches
  - Local caching for faster repeated searches
  - A more developer-friendly interface

## 🤝 Contributing

Found a bug? Have a feature idea? PRs are welcome! Just remember - the main programming language is English (via LLM), so start with a good description of what you want to achieve!

## 📜 License

MIT - Because sharing is caring!

---

Made with 🧠 AI and ❤️ for frustrated email searchers everywhere!
