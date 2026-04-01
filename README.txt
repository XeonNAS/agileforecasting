Fix: Azure DevOps whole-team days off were not reducing sprint capacity

What this patch does
- Adds a runtime patch file named `sitecustomize.py`
- When the app asks Azure DevOps for sprint capacities, it also fetches the
  iteration's whole-team days off and merges those into each team member's
  `daysOff`
- This makes the existing capacity calculation treat Easter/team holidays as
  unavailable days instead of showing the full sprint as available

Why this fixes your case
- Azure DevOps stores whole-team holidays separately from personal days off
- Iteration 08 shows 2 team days off in ADO
- If the app only used member capacity data, it would still show the full 9 days
- After this patch, those 2 team days off are applied, so the sprint should drop
  from 9 available days to 7 for unaffected team members

Apply
1. Open your repo root
2. Unzip this patch into the repo root so `sitecustomize.py` sits beside your
   existing folders such as `streamlit_app` and `src`
3. Start the app from that same repo root:

   streamlit run streamlit_app/app.py

Notes
- No source files are overwritten
- To remove the patch later, just delete `sitecustomize.py`
- Optional debug logging:

   MC_DEBUG_TEAMDAYSOFF_PATCH=1 streamlit run streamlit_app/app.py
