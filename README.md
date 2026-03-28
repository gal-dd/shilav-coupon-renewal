# Shilav coupon page watcher

This project checks the Shilav coupon landing page every 30 minutes and sends an email when the known "out of stock" text disappears or changes.

## What it checks

The watcher currently treats this text as the known out-of-stock message:

- הביקוש לערכה היה עצום והמלאי אזל תוך זמן קצר
- נעדכן בקרוב על מועד חידוש מלאי הערכות
- תודה על ההבנה ❤️

If that exact message is no longer found anywhere in the page text, the script treats it as a change and sends an email.

## Files

- `watcher.py` - the monitoring script
- `.github/workflows/check-shilav-stock.yml` - scheduled workflow
- `requirements.txt` - Python dependencies
- `state.json` - persisted state between workflow runs

## Local test

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
python watcher.py
```

## GitHub setup

1. Create a new GitHub repository.
2. Upload all project files.
3. In the repository, go to **Settings -> Secrets and variables -> Actions**.
4. Add these repository secrets:

- `SMTP_USER` = your Gmail address
- `SMTP_PASSWORD` = your Gmail app password
- `EMAIL_FROM` = the sender email address
- `EMAIL_TO` = the destination email address

5. Push the files to the default branch.
6. Go to **Actions** and enable workflows if GitHub asks.
7. Run the workflow once manually with **Run workflow** to test it.
8. After that, GitHub Actions will run on schedule.

## Gmail app password

For Gmail SMTP with app passwords:

- Turn on 2-Step Verification on your Google account.
- Create an app password.
- Use:
  - SMTP host: `smtp.gmail.com`
  - SMTP port: `465`
  - Username: your full Gmail address
  - Password: the generated app password

## Notes

- The schedule is set to minute 7 and 37 of every hour to reduce the chance of GitHub's schedule delays at the start of the hour.
- Scheduled GitHub Actions can still be delayed occasionally.
- The script only sends one email per detected changed text and will reset when the page returns to the original out-of-stock text.
