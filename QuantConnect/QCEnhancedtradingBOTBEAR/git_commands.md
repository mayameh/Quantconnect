
# Github command line

# Check if remote exists
git remote -v

# If empty, add your GitHub repo (replace with your actual repo URL)
git remote add origin https://github.com/YOUR_USERNAME/Quantconnect.git

# Fix origin to HTTPS
git remote set-url origin https://github.com/mayameh/Quantconnect.git

# Then push
git push -u origin main

cd ~/Library/CloudStorage/OneDrive-Personal/Mayank/Quantconnect
git add -A
git commit -m "Bear dip-buy strategy: NASDAQ-100 universe, bounce/volume/breadth filters, scale-in, optimized exits"
git push -u origin main

Your origin is using SSH (which is failing), but Quantconnect remote is HTTPS (which will work). Two options:

# Option A — Push using the HTTPS remote (quickest)
git push -u Quantconnect main


# Option B — Fix origin to use HTTPS
git remote set-url origin https://github.com/mayameh/Quantconnect.git
git push -u origin main