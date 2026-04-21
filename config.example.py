# config.py — copy this file to config.py and fill in your values
#
#   cp config.example.py config.py
#   nano config.py
#
# config.py is gitignored and will never be committed.

# ─── GitHub credentials ────────────────────────────────────────────────────────
# Create a token at: https://github.com/settings/tokens
# Required scopes: read:user, repo (for private repos)
GITHUB_TOKEN    = "ghp_your_token_here"
GITHUB_USERNAME = "your_username_here"

# ─── Refresh interval ──────────────────────────────────────────────────────────
REFRESH_INTERVAL = 900   # seconds (15 minutes)

# ─── Waveshare display model ────────────────────────────────────────────────────
# Match to your exact HAT variant. Common BWR options:
#   epd7in5b_V2  →  7.5" 800×480 BWR V2  (most common, recommended)
#   epd7in5b_V3  →  7.5" 800×480 BWR V3
#   epd5in83b_V2 →  5.83" 600×448 BWR V2
DISPLAY_MODEL = "epd7in5b_V2"

# ─── Display orientation ───────────────────────────────────────────────────────
# Set to True if your display renders upside-down
ROTATE_180 = False

# ─── Contribution level thresholds ─────────────────────────────────────────────
# Adjust to your commit frequency. These control the 5-level color gradient.
# [0] = no commits, [1-4] = progressively more commits
CONTRIB_THRESHOLDS = [0, 2, 5, 9]  # up to [n] = level N

# ─── Panel content limits ──────────────────────────────────────────────────────
REPOS_LIMIT = 7
FEED_LIMIT  = 7
