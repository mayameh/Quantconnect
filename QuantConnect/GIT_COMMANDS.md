# Git Add, Commit, and Push Commands

Use these commands from the git repo root. In this workspace, the bot folders are directories inside one shared repo, not separate git repositories.

## Standard flow

```bash
cd /path/to/your/repo
git status
git add -A
git commit -m "your commit message"
git push -u origin main
```

## If your branch is not `main`

```bash
git branch --show-current
git branch -a
git switch <branch-name>
git switch -c <new-branch-name>
git push -u origin <current-branch>
```

`git switch <branch-name>` moves you to an existing branch.
`git switch -c <new-branch-name>` creates the branch and switches to it.
If you want the branch to show up on GitHub, push the branch you are currently on. GitHub will not show a local branch until you push it.
If you want the changes on `main` instead, merge the feature branch into `main` and then push `main`.

If `git switch main` fails because local changes would be overwritten, use one of these options first:

```bash
git status
git add -A
git commit -m "Save local changes before switching branches"
git switch main
```

Or stash the changes temporarily:

```bash
git status
git stash push -m "temp before switching to main"
git switch main
git stash list
git stash pop
```

If the blocked file is only `QuantConnect/GIT_COMMANDS.md`, committing it first is usually the cleaner option.

## Create a new bot folder in this repo

If you want to create `tradingBOT-3SleeveHybridStrategy` as a new folder inside the QuantConnect repo, run this from the repo root:

```bash
cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect
mkdir -p QuantConnect/tradingBOT-3SleeveHybridStrategy
git status
git add QuantConnect/tradingBOT-3SleeveHybridStrategy
git commit -m "Add tradingBOT-3SleeveHybridStrategy folder"
git switch -c <new-branch-name>
git push -u Quantconnect <new-branch-name>
```

## Workspace folders

Use the matching block for the folder you are working in.

### EnhancedtradingBOT

```bash
cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/QuantConnect/EnhancedtradingBOT
git add -A
git commit -m "Update EnhancedtradingBOT"
git push -u origin main
```

### EnhancedtradingBOTAlpaca

```bash
cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/QuantConnect/EnhancedtradingBOTAlpaca
git add -A
git commit -m "Update EnhancedtradingBOTAlpaca"
git push -u origin main
```

### EnhancedtradingBOTVMLean

```bash
cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/QuantConnect/EnhancedtradingBOTVMLean
git add -A
git commit -m "Update EnhancedtradingBOTVMLean"
git push -u origin main
```

### EnhancedtradingBOT_Persistent_Test_Staging

```bash
cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/QuantConnect/EnhancedtradingBOT_Persistent_Test_Staging
git add -A
git commit -m "Update EnhancedtradingBOT_Persistent_Test_Staging"
git push -u origin main
```

### IBProd

```bash
cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/QuantConnect/IBProd
git add -A
git commit -m "Update IBProd"
git push -u origin main
```

### IBTrade

```bash
cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/QuantConnect/IBTrade
git add -A
git commit -m "Update IBTrade"
git push -u origin main
```

### QCEnhancedBOT_Persistence

```bash
cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/QuantConnect/QCEnhancedBOT_Persistence
git add -A
git commit -m "Update QCEnhancedBOT_Persistence"
git push -u origin main
```

### QCEnhancedMongoDBBOT

```bash
cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/QuantConnect/QCEnhancedMongoDBBOT
git add -A
git commit -m "Update QCEnhancedMongoDBBOT"
git push -u origin main
```

### QCEnhancedtradingBOT-LIVE

```bash
cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/QuantConnect/QCEnhancedtradingBOT-LIVE
git add -A
git commit -m "Update QCEnhancedtradingBOT-LIVE"
git push -u origin main
```

### QCEnhancedtradingBOTBEAR

```bash
cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/QuantConnect/QCEnhancedtradingBOTBEAR
git add -A
git commit -m "Update QCEnhancedtradingBOTBEAR"
git push -u origin main
```

### QCEnhancedtradingBOT_AI

```bash
cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/QuantConnect/QCEnhancedtradingBOT_AI
git add -A
git commit -m "Update QCEnhancedtradingBOT_AI"
git push -u origin main
```

### newtradingBOTAlpaca

```bash
cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/QuantConnect/newtradingBOTAlpaca
git add -A
git commit -m "Update newtradingBOTAlpaca"
git push -u origin main
```

### tradingBOT-3SleeveHybridStrategy

```bash
cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect/QuantConnect/tradingBOT-3SleeveHybridStrategy
git add -A
git commit -m "Update tradingBOT-3SleeveHybridStrategy"
git push -u Quantconnect feature/newtradingbotalpaca-intraday
```

## Remote setup and fixes

Use these when a repo does not yet have the right remote or push fails.

```bash
git remote -v
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git remote set-url origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

If your branch is already created locally, push that exact branch name:

```bash
git branch --show-current
git push -u origin <branch-name>
```

## Actual remotes in this workspace

From the current workspace root, these are the configured remotes and branch:

```bash
git remote -v
# origin      git@github.com:mayameh/Quantconnect.git
# Quantconnect https://github.com/mayameh/Quantconnect.git
git branch --show-current
# feature/newtradingbotalpaca-intraday
```

Exact push commands that work here:

```bash
git push -u Quantconnect feature/newtradingbotalpaca-intraday
git push -u origin feature/newtradingbotalpaca-intraday
```

If you want to switch `origin` to HTTPS first:

```bash
git remote set-url origin https://github.com/mayameh/Quantconnect.git
git push -u origin feature/newtradingbotalpaca-intraday
```

If a branch only exists locally and does not appear on GitHub yet, that is normal until you push it with `git push -u <remote> <branch-name>`. After that, GitHub will show the new branch.

If `git push -u origin main` returns `Permission denied (publickey)`, it means the SSH `origin` remote is not usable on this machine. Use the HTTPS `Quantconnect` remote above, or switch `origin` to HTTPS first.

If you accidentally typed the add command wrong, the correct form is:

```bash
git add -A
git commit -m "your message"
```