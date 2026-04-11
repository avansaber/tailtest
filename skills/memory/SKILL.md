---
description: View, inspect, or clear the tailtest validator memory file for this project. The memory file records what the validator has learned about this codebase across sessions.
argument-hint: [clear]
---

# /tailtest:memory

The validator subagent maintains a memory file at `.tailtest/memory/validator.md`. It appends dated notes after each validation pass -- what it checked, what it found, and any project-specific patterns worth remembering for next time.

## /tailtest:memory (no arguments) -- view the memory file

When the user invokes `/tailtest:memory` with no arguments:

1. Check if `.tailtest/memory/validator.md` exists.
   - If it does not exist: tell the user "No validator memory yet. The validator writes its first note after it runs for the first time. Set depth to thorough or paranoid to activate it."
   - If it exists: read the file and show its contents to the user. If the file is longer than 50 lines, show the most recent 50 lines and note how many lines were omitted.

2. After showing the file, tell the user:
   - How many entries it contains (count `---` separators as entry boundaries)
   - The date of the most recent entry (look for `**YYYY-MM-DD**` pattern)
   - "Run `/tailtest:memory clear` to archive this file and start fresh."

## /tailtest:memory clear -- archive and reset the memory file

When the user invokes `/tailtest:memory clear`:

1. Tell the user: "This will archive the current validator memory and start fresh. The archive is preserved at `.tailtest/memory/validator-archive-<date>.md`. Nothing is permanently deleted."
2. Ask for confirmation: "Clear the validator memory? (yes/no)"
3. If the user says yes, run:

```bash
python3 -c "
import sys, shutil
from pathlib import Path
from datetime import datetime, timezone

memory_path = Path('.tailtest/memory/validator.md')
if not memory_path.exists():
    print('no-memory-file')
    sys.exit(0)

date_str = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
archive_path = Path(f'.tailtest/memory/validator-archive-{date_str}.md')
shutil.copy2(memory_path, archive_path)
memory_path.write_text('', encoding='utf-8')
print(f'archived-to:{archive_path}')
"
```

4. Parse the output:
   - `no-memory-file`: tell the user "No memory file found -- nothing to clear."
   - `archived-to:<path>`: tell the user "Memory archived to `<path>`. The validator will start fresh on its next invocation."

## What not to do

- Do not delete the memory file -- always archive it first.
- Do not modify `.tailtest/memory/validator.md` manually other than as described above.
- Do not show archive files unless the user explicitly asks for them.

## Related skills

- `/tailtest:depth thorough` -- activate the validator
- `/tailtest disable-validator` -- disable the validator entirely
- `/tailtest enable-validator` -- re-enable the validator
