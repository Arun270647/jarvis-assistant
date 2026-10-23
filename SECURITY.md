# Security policy

Jarvis can act on the user's PC, so security problems matter to us. Thank you for reporting them privately.

## Reporting a vulnerability

Please **don't open a public issue**. Use GitHub's private reporting instead: on the repository's
**Security** tab, choose **Report a vulnerability**. Include:

- what an attacker can do, and what they need first (for example a web page the assistant reads, a file in a
  project folder, or the same network);
- steps to reproduce, with the version (Settings › About) and the AI provider used;
- logs or screenshots, with keys and personal data removed.

We aim to acknowledge a report within 7 days, and to agree on a fix and a disclosure date with you within 30
days. We credit reporters in the release notes unless you'd rather stay anonymous.

## Supported versions

Jarvis has not had a public release yet. Until it does, only the latest code on the `main` branch is
supported.

## Scope

Examples of what counts:

- anything that gets around the permission checks or approvals;
- prompt injection that makes the assistant act without the approval the rules require;
- keys or passwords reaching logs, history or the AI;
- the phone server accepting a connection it shouldn't;
- getting past the protected folders or the secret-file block.

Out of scope: problems that need full control of the user's Windows account already, an extension the user
installed themselves (extensions run with full user rights by design), and the behaviour of the AI provider
the user chose.

How the safeguards work is described in [docs/SECURITY_MODEL.md](docs/SECURITY_MODEL.md).
