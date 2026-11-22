# outputs/

Untracked. The GUI asks for a user name at startup and writes everything for
that user to `outputs/<user>/`: fund metrics, allocation chunks and summary,
`portfolio_viz.html`, `<user>_project.swp.json` and the exported CSVs.
Only this README is in git; CI fails if generated data is ever tracked.
