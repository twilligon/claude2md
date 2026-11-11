# `claude2md`

Convert Claude.ai or Claude Code chat exports to Markdown format.

`claude2md` converts chats exported from Claude.ai or Claude Code to Markdown format. This lets you efficiently show entire chats to other models or instances until you struggle to remember which transcript of a chat full of transcripts of chats full of transcripts of chats is the right one (aka slopception).

For Claude.ai chats, [`claude-backup`](https://github.com/twilligon/claude-backup) can fetch a local copy of your chats for `claude2md`. Give `claude2md` a chat's ID, name, or URL and it finds the chat in what `claude-backup` saved.

## Features

You can filter the exports to any subset of user, assistant, and thinking blocks. For context management I've found it's often helpful to only export user messages, though obviously for the strongest "continuity" between an exported chat transcript and its consumer you want all context from the prior chat.

Text attachments are inlined and other uploads linked; `--embed` inlines everything, `--no-embed` nothing.

You can branch conversations on Claude.ai (and to a lesser extent in Claude Code), and `claude2md` can export any branch. You can list the branches of a chat and their IDs with `claude2md --branches`, export a specific branch with `claude2md --branch ID`, or dump every branch at once with `claude2md --all-branches` (each line gets tagged with the IDs of the branches that contain it).

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
    usage: claude2md [-h] [-v] [-e] [-E] [--lossy] [-B | -b ID | -A] [-n] [-N]
                     [-u] [-a] [-t]
                     [chat]

    Convert Claude.ai or Claude Code chats to Markdown

    positional arguments:
      chat                chat JSON, UUID, name, or URL (default: stdin)

    options:
      -h, --help          show this help message and exit
      -v, --version       show program's version number and exit
      -e, --embed         embed media as data URLs
      -E, --no-embed      link text attachments like media
      --lossy             make do without what the backup lacks

    branches:
      -B, --branches      list all branches
      -b, --branch ID     use a specific non-default branch
      -A, --all-branches  show messages from all branches

    filters:
      -n, --name          always show chat name (or "Untitled")
      -N, --no-name       never show chat name
      -u, --user          show user messages
      -a, --assistant     show assistant messages
      -t, --thinking      show thinking blocks

`claude2code` writes the chat as a Claude Code session and prints the session's ID, so `claude --resume "$(claude2code 'Chat name')"` carries on with the chat in Claude Code. PDF pages the chat read are rendered with `pdftoppm`.

    $ claude2code --help
    usage: claude2code [-h] [-v] [-s PROMPT] [--lossy] [-B | -b ID] [chat]

    Import a Claude.ai chat as a Claude Code session

    positional arguments:
      chat                 chat JSON, UUID, name, or URL (default: stdin)

    options:
      -h, --help           show this help message and exit
      -v, --version        show program's version number and exit
      -s, --system PROMPT  use a custom system prompt
      --lossy              make do without what the backup lacks

    branches:
      -B, --branches       list all branches
      -b, --branch ID      use a specific non-default branch

## Examples

#### Claude.ai

    $ claude2md -uat c6a33008 | head -n20
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

    $ claude2md ~/.claude/projects/-home-claude-claude2md/0c8de429-fb29-4096-925e-6d81a0437f09.jsonl | head -n3
    > rename --leaves and --leaf to --branches and --branch. no need to change how we refer to this stuff internally, only user facing matters. in @claude2md/__init__.py . ultrathink

    I'll help you rename the command-line arguments from `--leaves`/`--leaf` to `--branches`/`--branch` in the user-facing interface. Let me start by creating a todo list for this task.

## License

`claude2md` is dedicated to the public domain where possible via CC0-1.0.
