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
git push -u origin <current-branch>
```

## Workspace repos

Use the matching block for the repo you are working in.

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
git push -u origin main
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

If you accidentally typed the add command wrong, the correct form is:

```bash
git add -A
git commit -m "your message"
```