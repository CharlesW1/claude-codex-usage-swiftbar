#!/usr/bin/env python3
# <xbar.title>Claude + Codex + Antigravity Usage</xbar.title>
# <xbar.version>v2.0.0</xbar.version>
# <xbar.desc>Claude Code, OpenAI Codex, and Google Antigravity 5-hour and weekly usage limits in the menu bar.</xbar.desc>
# <swiftbar.hideAbout>true</swiftbar.hideAbout>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from claude_usage import main  # noqa: E402

if __name__ == "__main__":
    main()
