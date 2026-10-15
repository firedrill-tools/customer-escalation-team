# Agent notes

Keep case identity in `CaseContext` across handoffs and record ownership only through SDK handoff callbacks. Triage must not gain specialist write tools. Recheck permissions, owner and revision inside each action, including after a handoff. Never claim a credit or unlock before the tool succeeds. Keep fixtures synthetic and do not commit credentials or runtime state. Scripted SDK runs are offline orchestration proof, not live OpenAI or Firedrill evidence.
