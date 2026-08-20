"""Server-side AI helpers for the CRM.

`crm.ai.client.complete` is the single provider-agnostic entry point. Import it
lazily inside the calling function so that a site without AI configured never
pays for the import.
"""
