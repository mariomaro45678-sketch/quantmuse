---
trigger: always_on
---

---
trigger: always_on
---

AGENT PROTOCOL: STATE PERSISTENCE & LOGGING SYSTEM
1. FILE SYSTEM AUTHORITY
You interact with three specific files

_PROJECT_LOG.md (THE BRAIN): Contains only the high-level project state, active tasks, and the last 500 lines of logs.Log frequently, even mid session, and always put the latest log at the top, so the oldest ones move downa and gets moved when the file reaches 700 or more lines). This is your primary context. when the file contains more than 700 lines, move the oldest 300 lines (do not truncate logs, always move them when they are complete) to PROJECT_LOG_ARCHIVE.md

_PROJECT_LOG_ARCHIVE.md (THE HISTORY): A chronological storage for old logs.(NEVER TRUNCATE FOR BREVITY. IT MUST KEEPS LOGS FOR EVERY PAST ACTION MOVED FROM PROJECT_LOG.md)

_MASTER_TASK.md  (THE TRACKER): instead of creating and updating one task file per session, you will use this file to create the tasks for your work and to mark them as complete. You must do this before starting to code and before closing the message. for each task in the master task file, create granular subtasks that trak better the progress and what to do.

1.1 INITIALIZATION RULE (CRITICAL)
Before doing anything else, check if _PROJECT_LOG_ARCHIVE.md exists.



2. START-OF-SESSION PROTOCOL
Step 1: Ingest Context. Read _PROJECT_LOG.md from top to bottom. Step 2: Acknowledge State. internally identify:

Current Architecture: (e.g., Node.js, Railway, Postgres).

Active Tasks: What is currently "In Progress"?

Recent Changes: Check the last  logs for bugs or partial fixes.

3. END-OF-SESSION PROTOCOL (LOGGING)
At the end of your turn, you MUST append a log entry to _PROJECT_LOG.md.

3.1 COMPACT LOG FORMAT
Do NOT write paragraphs. Use this strict, compressed format to save tokens:

Markdown
## [YYYY-MM-DD HH:MM] 🤖 [Model Name] | 🎯 [Short Goal Summary]
- **Changes**: `filename.js` (Added/Fixed X), `style.css` (Refactored Y).
- **Context**: [CRITICAL info only: Next steps, broken tests, or API limits].
4. THE "GARBAGE COLLECTOR" RULE (Auto-Archiving)



UPDATE STATE: If those old logs contained important architectural decisions (e.g., "We switched to JWT"), ensure this info is summarized in the "Current State" section of the main file before deleting the logs.



5. FILE STRUCTURE TEMPLATE
You must maintain _PROJECT_LOG.md in exactly this structure. If the file is messy, REFORMAT it to match this:

Markdown
# 🚀 FOOTBALL DATABASE - PROJECT LOG

## 🧠 CURRENT STATE (Source of Truth)
* **Stack**: Node.js, PostgreSQL (Railway), React (Vite).
* **Live URL**: https://football-api-production-dcb9.up.railway.app
* **Key Decisions**:
    * Auth: JWT based.
    * Database: Uses `jsonb` for historic match data.
    * API Limits: Strict cap at 1800 req/hr (SmartUpdater + MasterSync).

## ⚠️ KNOWN PITFALLS (Do Not Repeat Errors)
* **Date Formatting**: NEVER use `weekday: 'lowercase'`. Use `{ weekday: 'long' }` and `.toLowerCase()`.

## 📋 ACTIVE TASKS (Queue)
* [ ] Task A (In Progress)
* [ ] Task B (Pending)

## 📝 RECENT SESSION LOGS (Rolling Window)
[...Newest logs go here...]

when moving the old logs to project log archive, make sure that those logs are actually the oldest ones, do not move most recent logs.