# `claude2md`

Convert Claude.ai or Claude Code chat exports to Markdown format.

`claude2md` converts chats exported from Claude.ai or Claude Code to Markdown format. This lets you efficiently show entire chats to other models or instances until you struggle to remember which transcript of a chat full of transcripts of chats full of transcripts of chats is the right one (aka slopception).

For Claude.ai chats, [`claude-backup`](https://github.com/twilligon/claude-backup) is the companion tool that fetches the JSON files `claude2md` reads. For Claude Code, the JSONL files live under `~/.claude/projects/`.

## Features

You can filter the exports to any subset of user, assistant, and thinking blocks. For context management I've found it's often helpful to only export user messages, though obviously for the strongest "continuity" between an exported chat transcript and its consumer you want all context from the prior chat. Though Claude typically does not get to see its past thinking, so `--no-thinking` (our default) ought to be just as good if not better than including thinking blocks (by virtue of being on-distribution).

We try to include Claude.ai attachments when they're available in the export, but the format we parse right now does not have them embedded in the JSON or anything, so in practice only attachments with `extracted_content`, i.e. text files, markdown, etc. will be present in the transcripts.

You can branch conversations on Claude.ai (and to a lesser extent in Claude Code), and `claude2md` can export any branch. You can list the branches of a chat and their UUIDs with `claude2md --branches` and export a specific branch with `claude2md --branch UUID`.

We don't support "Claude Code on the Web" yet, though ostensibly if you "teleport" a Web session to a local Claude Code instance, you can export the history as normal from there.

## Install

The blazing fast and memory safe way:

    $ uvx claude2md --help  # installed on demand

The traditional way:

    $ pip install claude2md
    $ claude2md --help

The bleeding-edge way:

    $ git clone https://github.com/twilligon/claude2md
    $ cd claude2md
    $ python3 -m venv venv; . venv/bin/activate  # you probably want a venv
    $ pip install -e .
    $ claude2md --help

## Usage

    $ claude2md --help
    usage: claude2md [-h] [--branches | --branch UUID] [--user] [--no-user]
                     [--assistant] [--no-assistant] [--thinking]
                     [--no-thinking] [--title] [--no-title]
                     [file]

    Convert Claude.ai or Claude Code chats to Markdown

    positional arguments:
      file            JSON file to convert (default: stdin)

    options:
      -h, --help      show this help message and exit
      --branches      List all branch message UUIDs
      --branch UUID   Show chain to specific branch
      --user          Show user messages (default)
      --no-user       Hide user messages
      --assistant     Show assistant (default)
      --no-assistant  Hide assistant
      --thinking      Show thinking blocks
      --no-thinking   Hide thinking (default)
      --title         Always show title (or "Untitled" if none)
      --no-title      Never show title

## Examples

#### Claude.ai

    $ claude2md --title --thinking ~/.local/share/claude-backup/f50d0792-fa79-4d2d-a945-c8f709e9e7a7/c6a33008-39b3-45ed-a00e-413141c89a90.json | head -n20
    # Python packaging complexity

    > why is python packaging such a clusterfuck? ultrathink

    <thinking>
    The user is asking about Python packaging and why it's so complicated/messy. They've also said "ultrathink" which I interpret as wanting a very thorough, deep analysis.

    This is a great question that many Python developers have strong feelings about. Let me think through the historical and technical reasons why Python packaging became so complex.

    Key issues to cover:
    1. Historical evolution - how we got here
    2. Multiple competing tools and standards
    3. The various problems each tool tries to solve
    4. Why there's no single "right" way
    5. The current state and attempts to fix things

    Let me structure this thoughtfully and comprehensively since they asked for "ultrathink."
    </thinking>

    Python packaging *is* notoriously complex, and there are deep historical and technical reasons for this. Let me break down why it became such a mess:

#### Claude Code

    $ claude2md ~/.claude/projects/-home-claude-claude2md/0c8de429-fb29-4096-925e-6d81a0437f09.jsonl --branch 670e0906-013b-4c83-b7c7-ecf8e12aab3e | head -n3
    > rename --leaves and --leaf to --branches and --branch. no need to change how we refer to this stuff internally, only user facing matters. in @claude2md/__init__.py . ultrathink

    I'll help you rename the command-line arguments from `--leaves`/`--leaf` to `--branches`/`--branch` in the user-facing interface. Let me start by creating a todo list for this task.

## License

`claude2md` is dedicated to the public domain where possible via CC0-1.0.
