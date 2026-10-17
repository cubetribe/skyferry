## What does this change?

<!-- One or two sentences. Link the issue if there is one (Fixes #123). -->

## How was it tested?

- [ ] `python3 -m unittest discover -s tests -v` passes locally (rclone in PATH)
- [ ] New behaviour is covered by a test

## Safety checklist

- [ ] No secrets, tokens, ping URLs, ntfy topics, hostnames or personal paths in the diff
- [ ] Nothing can delete or modify data on the **source** cloud (only the documented rename)
- [ ] Buffer data is only released after remote verification
