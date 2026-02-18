# QuantMuse Project Instructions

## Memory & Self-Improvement Protocol

**CRITICAL: You MUST follow this auto-update protocol during EVERY session:**

### Auto-Update Files (NO user prompting required)
Located at: `~/.claude/projects/-home-pap-Desktop-QuantMuse/memory/`

1. **lessons.md** - Update IMMEDIATELY when:
   - Finding ANY bug, error, or wrong assumption
   - User corrects you or shows confusion
   - Fixing production issues
   - Discovering gotchas or unexpected behavior

2. **patterns.md** - Update when:
   - Creating useful debugging commands
   - Solving problems elegantly  
   - Finding reusable approaches or shortcuts

3. **prefs.md** - Update when:
   - User expresses preference or frustration
   - Observing consistent user behavior (3+ times)
   - User explicitly approves something

**Rules:**
- Use Edit tool to update existing entries, Write for new ones
- Be specific: include file paths, commands, error messages
- Date-stamp new entries

### Session Protocol

**START every session:**
```bash
ps aux | grep -E "news_collector|run_multi"
tail -20 logs/*.log
cat /reports/HANDOFF.md  # if exists
