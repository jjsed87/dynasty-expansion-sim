#!/usr/bin/env python3

import streamlit as st
import requests
import json
import pandas as pd
import openai

st.set_page_config(layout="wide")
st.title("Dynasty League Expansion Draft Simulator")

# --- Data loading ---
@st.cache_data
def load_league_data(league_id):
    # Fetch data from Sleeper API
    rosters = requests.get(f"https://api.sleeper.app/v1/league/{league_id}/rosters").json()
    players = requests.get("https://api.sleeper.app/v1/players/nfl").json()
    users   = requests.get(f"https://api.sleeper.app/v1/league/{league_id}/users").json()

    # Build player mappings
    id_to_name = {}
    id_to_pos  = {}
    for pid, pdata in players.items():
        full = pdata.get("full_name") or f"{pdata.get('first_name','')} {pdata.get('last_name','')}".strip()
        id_to_name[pid] = full
        id_to_pos[pid]  = pdata.get("position","UNK")

    # Build owner→team name map
    id_to_team = {u["user_id"]: u["display_name"] for u in users}

    # Build owner→roster map
    league_rosters = {
        team["owner_id"]: team.get("players") or []
        for team in rosters
    }

    return league_rosters, id_to_name, id_to_pos, id_to_team

# --- Simulation & draft (manual) ---
def simulate_and_draft(rosters, id_to_name, id_to_pos,
                       max_protect, pos_caps, num_teams, picks_per_team,
                       draft_format, protection_overrides):
    breakdown = {}
    pool      = []

    for owner, roster_ids in rosters.items():
        # apply manual or AI-provided protections
        protected = protection_overrides.get(owner, roster_ids[:max_protect])[:max_protect]
        candidates = [pid for pid in roster_ids if pid not in protected]

        # enforce per-position caps on losses
        losses = []
        by_pos = {}
        for pid in candidates:
            pos = id_to_pos.get(pid, "UNK")
            by_pos.setdefault(pos, []).append(pid)
        for pos, pids in by_pos.items():
            cap = pos_caps.get(pos, len(pids))
            losses.extend(pids[:cap])

        breakdown[owner] = {
            "protected": [id_to_name[p] for p in protected],
            "losses":    [id_to_name[p] for p in losses]
        }
        pool.extend(losses)

    total_picks    = num_teams * picks_per_team
    draft_pool_ids = pool[:total_picks]

    # actual draft order (snake or linear)
    teams = [f"Expansion Team {i+1}" for i in range(num_teams)]
    picks = {t: [] for t in teams}
    for idx, pid in enumerate(draft_pool_ids):
        if draft_format == "Snake":
            rnd   = idx // num_teams
            order = teams if rnd % 2 == 0 else list(reversed(teams))
            team  = order[idx % num_teams]
        else:
            team = teams[idx % num_teams]
        picks[team].append(pid)

    return breakdown, draft_pool_ids, picks

# --- AI helpers ---
def ai_protect(roster_ids, id_to_name, id_to_pos, max_protect, pos_caps):
    roster_list = [{"name": id_to_name[p], "position": id_to_pos[p]} for p in roster_ids]
    prompt = (
        "You are a veteran fantasy football general manager. "
        f"Here is the roster: {json.dumps(roster_list)}. "
        f"You must choose exactly {max_protect} players to PROTECT. "
        f"You should prioritize based on historical performance, positional scarcity, and future upside. "
        f"Also respect these maximum losses per position: {json.dumps(pos_caps)}. "
        "Respond with a JSON array of player names to protect."
    )
    resp = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {"role":"system","content":"You help pick the best fantasy roster protections."},
            {"role":"user","content":prompt}
        ],
        temperature=0.2
    )
    try:
        names = json.loads(resp.choices[0].message.content)
    except Exception:
        return roster_ids[:max_protect]  # fallback
    # map back to IDs
    name_to_id = {v:k for k,v in id_to_name.items()}
    return [name_to_id[n] for n in names if n in name_to_id][:max_protect]

def ai_draft(pool_ids, id_to_name, num_teams, picks_per_team, draft_format):
    pool_list = [id_to_name[p] for p in pool_ids]
    prompt = (
        "You are a fantasy draft strategist. "
        f"Available players: {json.dumps(pool_list)}. "
        f"Draft {picks_per_team} rounds for {num_teams} expansion teams in a {draft_format} format. "
        "Balance positional needs and player value. "
        "Return a JSON object mapping 'Expansion Team 1', 'Expansion Team 2', etc. to arrays of player names."
    )
    resp = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[
            {"role":"system","content":"You help conduct expansion drafts."},
            {"role":"user","content":prompt}
        ],
        temperature=0.2
    )
    try:
        mapping = json.loads(resp.choices[0].message.content)
    except Exception:
        # fallback: simple snake
        teams = [f"Expansion Team {i+1}" for i in range(num_teams)]
        mapping = {team: [] for team in teams}
        for idx, pid in enumerate(pool_ids[: num_teams * picks_per_team]):
            team = teams[idx % num_teams] if draft_format=="Linear" else (
                teams if (idx//num_teams)%2==0 else list(reversed(teams))
            )[idx%num_teams]
            mapping[team].append(id_to_name[pool_ids[idx]])
    # convert names → IDs
    name_to_id = {v:k for k,v in id_to_name.items()}
    return {
        team: [name_to_id.get(n) for n in names if name_to_id.get(n)]
        for team, names in mapping.items()
    }

# --- UI ---
league_id = st.text_input("🔢 Enter your Sleeper League ID", value="1186327865394335744")

if league_id:
    rosters, id_to_name, id_to_pos, id_to_team = load_league_data(league_id)

    with st.expander("⚙️ Settings", expanded=True):
        max_protect    = st.slider("Max Protected per Team", 0, 20, 12)
        num_teams      = st.number_input("Expansion Teams", 1, 4, 2)
        picks_per_team = st.number_input("Picks per Expansion Team", 1, 50, 25)
        draft_format   = st.radio("Draft Format", ["Snake", "Linear"], index=0)

        st.write("**Position Loss Caps**")
        rb_cap   = st.number_input("RB cap", 0, 10, 2)
        wr_cap   = st.number_input("WR cap", 0, 10, 3)
        te_cap   = st.number_input("TE cap", 0, 10, 1)
        qb_cap   = st.number_input("QB cap", 0, 5, 1)
        flex_cap = st.number_input("Other positions cap", 0, 10, 5)
        pos_caps = {"RB": rb_cap, "WR": wr_cap, "TE": te_cap, "QB": qb_cap}
        for p in set(id_to_pos.values()):
            if p not in pos_caps:
                pos_caps[p] = flex_cap

        st.write("**Manual Protection Overrides**")
        protection_overrides = {}
        for owner, roster_ids in rosters.items():
            team_label = id_to_team.get(owner, f"Expansion Team {owner}")
            protection_overrides[owner] = st.multiselect(
                f"{team_label} protects",
                roster_ids,
                default=roster_ids[:max_protect],
                format_func=lambda pid: id_to_name.get(pid, pid)
            )

        use_ai = st.checkbox("🤖 Use AI for protections & draft")
        run    = st.button("▶️ Run Simulation & Draft")

    if run:
        # Validate manual protections
        if not use_ai:
            invalid = [o for o,p in protection_overrides.items() if len(p)!=max_protect]
            if invalid:
                st.error(
                    f"Each team must protect exactly {max_protect} players! "
                    f"Check: {', '.join(id_to_team.get(o,f'Owner {o}') for o in invalid)}"
                )
                st.stop()
            final_protected = protection_overrides
        else:
            openai.api_key = st.secrets["openai"]["api_key"]
            # display what AI selects
            st.subheader("🤖 AI‑Selected Protections")
            final_protected = {}
            for owner, roster in rosters.items():
                picks = ai_protect(roster, id_to_name, id_to_pos, max_protect, pos_caps)
                final_protected[owner] = picks
                team_label = id_to_team.get(owner, f"Expansion Team {owner}")
                st.write(f"**{team_label}:**", [id_to_name.get(p) for p in picks])

        # simulate & draft
        breakdown, pool_ids, picks_by_team = simulate_and_draft(
            rosters, id_to_name, id_to_pos,
            max_protect, pos_caps, num_teams, picks_per_team,
            draft_format, final_protected
        )

        if use_ai:
            # run AI draft too
            picks_by_team = ai_draft(pool_ids, id_to_name, num_teams, picks_per_team, draft_format)

        # Display results
        tab1, tab2, tab3, tab4 = st.tabs([
            "Team Breakdown","Draft Pool","Draft Results","Expansion Rosters"
        ])

        with tab1:
            df1 = pd.DataFrame([
                {
                    "Team":      id_to_team.get(o, f"Owner {o}"),
                    "Protected": ", ".join(b["protected"]),
                    "Losses":    ", ".join(b["losses"])
                }
                for o,b in breakdown.items()
            ])
            st.dataframe(df1, use_container_width=True)
            st.download_button("Download Breakdown CSV", df1.to_csv(index=False), "breakdown.csv")

        with tab2:
            df2 = pd.DataFrame({"Player": [id_to_name[p] for p in pool_ids]})
            st.dataframe(df2, use_container_width=True)
            st.download_button("Download Pool CSV", df2.to_csv(index=False), "pool.csv")

        with tab3:
            rows=[]
            num=1
            for team, pids in picks_by_team.items():
                for pid in pids:
                    rows.append({"Pick": num, "Team": team, "Player": id_to_name.get(pid,pid)})
                    num+=1
            df3 = pd.DataFrame(rows)
            st.dataframe(df3, use_container_width=True)
            st.download_button("Download Draft Results CSV", df3.to_csv(index=False), "results.csv")

        with tab4:
            for team, pids in picks_by_team.items():
                st.subheader(team)
                df4 = pd.DataFrame([
                    {"Player": id_to_name.get(pid,pid), "Position": id_to_pos.get(pid,"UNK")}
                    for pid in pids
                ])
                st.table(df4)
                st.download_button(
                    f"Download {team} Roster CSV",
                    df4.to_csv(index=False),
                    f"{team.replace(' ','_')}_roster.csv"
                )

        st.success("✅ Done! Adjust settings to simulate again.")

