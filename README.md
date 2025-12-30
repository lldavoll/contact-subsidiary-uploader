# Upload Contacts and Subsidiaries to Firebase

This tool takes company contact info and subsidiary relationships from CSV files and puts them into Firebase.

## What It Does

- **Cleans up bad data**: Skips rows that look wrong or incomplete
- **Finds matching companies**: Compares company names even if they're spelled slightly differently
- **Lets you test first**: Run it without actually changing anything in Firebase
- **Shows you what needs review**: Creates a list of matches that need a human to double-check
- **Works one company at a time**: Test on a single company before doing everything

## Getting Started

### Step 1: Install Python Packages

First, install the Python packages you need:

```bash
pip install -r requirements.txt
```

### Step 2: Set Up Firebase

You need to set up Firebase before you can upload data. Here's how:

1. **Go to Firebase Console**
   - Visit https://console.firebase.google.com/
   - Sign in with your Google account
   - Select your project (or create a new one if needed)

2. **Get Your Project ID**
   - In Firebase Console, click the gear icon next to "Project Overview"
   - Go to "Project settings"
   - Your Project ID is shown at the top (you'll need this)

3. **Create a Service Account**
   - Still in Project settings, go to the "Service accounts" tab
   - Click "Generate new private key"
   - This downloads a JSON file with your credentials
   - **Important**: Keep this file safe - it has full access to your Firebase project

4. **Save the Credentials File**
   - Put the downloaded JSON file somewhere safe on your computer
   - A good place is in the same folder as this tool: `./firebase-credentials.json`
   - Remember the full path to this file - you'll need it when running the tool

5. **Check Permissions**
   - Make sure your service account has permission to read and write to Firestore
   - In Firebase Console, go to Firestore Database
   - Check the Rules tab to make sure writes are allowed (or adjust for your needs)

**Note**: Never commit the credentials JSON file to git - it's already in .gitignore to prevent this.

## How to Use It

### Try It Out First (Recommended)

Before uploading anything, test it to see what would happen:

```bash
python uploader.py \
  --contacts-csv company_contacts_full.csv \
  --subsidiary-csv company_subsidiary.csv \
  --firebase-credentials ./firebase-credentials.json \
  --firebase-project my-firebase-project \
  --dry-run
```

**Note**: Replace `./firebase-credentials.json` with where you put your credentials file, and `my-firebase-project` with your actual Firebase project ID.

This shows you what would be uploaded without actually changing anything.

### Test With One Company

Start with just one company to make sure everything works:

```bash
python uploader.py \
  --contacts-csv company_contacts_full.csv \
  --subsidiary-csv company_subsidiary.csv \
  --firebase-credentials ./firebase-credentials.json \
  --firebase-project my-firebase-project \
  --single-company "3M Co" \
  --dry-run
```

**Note**: Replace the credentials path and project ID with your actual values. You can use any company name for testing.

### Actually Upload Data

Once you're happy with the test results, remove the `--dry-run` flag:

```bash
python uploader.py \
  --contacts-csv company_contacts_full.csv \
  --subsidiary-csv company_subsidiary.csv \
  --firebase-credentials ./firebase-credentials.json \
  --firebase-project my-firebase-project
```

**Note**: Make sure to use your actual credentials file path and project ID.

### Change How Strict the Matching Is

If you want to be more or less picky about matching company names:

```bash
python uploader.py \
  --contacts-csv company_contacts_full.csv \
  --subsidiary-csv company_subsidiary.csv \
  --firebase-credentials ./firebase-credentials.json \
  --firebase-project my-firebase-project \
  --auto-accept-threshold 92.0 \
  --manual-review-threshold 85.0
```

**Note**: Replace with your actual credentials path and project ID. The thresholds are percentages (92.0 = 92%).

## Command Options

- `--contacts-csv`: Where your contacts CSV file is (required)
- `--subsidiary-csv`: Where your subsidiaries CSV file is (required)
- `--firebase-credentials`: Path to your Firebase credentials JSON file (required)
- `--firebase-project`: Your Firebase project ID (optional - sometimes it's in the credentials file)
- `--dry-run`: Test mode - shows what would happen without actually doing it
- `--single-company`: Only process this one company (good for testing)
- `--auto-accept-threshold`: How sure it needs to be to auto-upload (default: 90%)
- `--manual-review-threshold`: How sure it needs to be to ask you to review (default: 80%)
- `--output-dir`: Where to save the review files (default: current folder)

## What Files It Creates

After running, you'll get two files:

1. **manual_review.json**: Companies where the match wasn't super clear (80-89% match)
   - Shows what it thinks the match is, plus other options
   - You can approve it, pick a different match, or reject it

2. **unmatched_companies.json**: Companies it couldn't match at all (less than 80% match)
   - These need you to figure out manually
   - You can try different company names and run it again

## Where Data Goes in Firebase

### Contact Info

Contact info goes into each brand's `social` field. It looks like this:

```json
{
  "twitter": "https://twitter.com/company",
  "facebook": "https://facebook.com/company",
  "ir_email": "investors@company.com",
  "cs_email": "support@company.com",
  "ir_page": "https://company.com/investors",
  "cs_page": "https://company.com/contact",
  "website": "company.com"
}
```

### Subsidiary Info

Subsidiary relationships are stored in three places:

1. **On the parent company**: A list of all its subsidiaries
   ```json
   {
     "subsidiary_1_id": true,
     "subsidiary_2_id": true
   }
   ```

2. **On each subsidiary**: The parent company's name
   - Field: `parent_company` (just the name as text)

3. **On each subsidiary**: The parent company's ID
   - Field: `parent_id` (the brand ID as text)

## How It Handles Bad Data

The tool automatically skips:

1. **Rows that look wrong**:
   - Subsidiaries that got matched to the wrong parent company
   - Text that's clearly not a company name (like "The following is a list...")
   - Headers or labels that got mixed in with the data

2. **Incomplete rows**:
   - Rows that say there are subsidiaries but don't list any
   - These get logged so you know about them, but they're not uploaded

## How It Matches Company Names

The tool is pretty smart about matching names even if they're not exactly the same:

- **90% or higher match**: It's confident, so it uploads automatically
- **80-89% match**: It's pretty sure but wants you to double-check
- **Less than 80% match**: It's not sure, so it skips it and puts it in the unmatched list

It tries a few different ways to compare names and picks the best match.

## How It Cleans Up Company Names

Before comparing names, it:
- Makes everything lowercase
- Removes punctuation
- Removes common endings like "Inc", "Corp", "LLC"
- Changes symbols to words (& becomes "and", @ becomes "at")
- Removes extra spaces

## Reviewing Matches

1. Run it with `--dry-run` first to see what it finds
2. Open `manual_review.json` and look through the matches
3. For each one, you can:
   - Accept the match (looks good)
   - Pick a different match from the options
   - Reject it (doesn't look right)
4. Use the review tool to process your decisions

## Common Problems

### Can't Connect to Firebase

- Make sure the path to your credentials file is correct
- Check that your service account has permission to read and write to Firestore
- Double-check your project ID matches what's in Firebase Console
- Make sure the credentials JSON file is valid (not corrupted)
- Try regenerating the service account key if nothing else works is right

### Not Finding Matches

- Make sure you actually have brands in Firebase
- The tool looks for company names in fields like 'name', 'company_name', etc.
- Company names might be too different - check the unmatched list
- Try lowering the review threshold if you're getting too many unmatched

### Running Slow

- Test with `--single-company` first
- The tool loads all brands into memory to go faster
- Big files take time - just wait and watch the progress messages

## Safety Features

- **Test mode**: See what would happen without actually doing it
- **One at a time**: Try a single company first
- **Shows you everything**: You can see exactly what it's going to change
- **Asks for help**: When it's not sure, it asks you to check
- **Keeps going**: If one update fails, it doesn't stop everything

## Getting Started

1. Test it first with `--dry-run`
2. Try one company with `--single-company`
3. Look at the output files it creates
4. Adjust the matching thresholds if needed
5. When you're ready, run it for real (remove `--dry-run`)

If you run into problems, ask for help!

# contact-subsidiary-uploader
