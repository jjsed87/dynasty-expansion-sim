#!/usr/bin/env python3

import streamlit as st
import requests
import json
import pandas as pd
from openai import OpenAI

st.set_page_config(layout="wide")
st.title("Dynasty League Expansion Draft Simulator")

# --- Data loading functions ---
@st.cache_data
def load_league_data(league_id):
    rosters = requests.get(f"https://api.sleeper.app/v1/league/{league_id}/rosters").json()
    players = requests.get("https://api.sleeper.app/v1/players/nfl").json()
    users = requests.get(f"https://api.sleeper.app/v1/league/{league_id}/users").json()

    id_to_name = {}
    id_to_pos = {}
    for pid, pdata in players.items():
        full = pdata.get("full_name") or f"{pdata.get('first_name', '')} {pdata.get('last_name', '')}".strip()
        id_to_name[pid] = full
        id_to_pos[pid] = pdata.get("position", "UNK")

    id_to_team = {u["user_id"]: u["display_name"] for u in users}
    league_rosters = {team["owner_id"]: team.get("players") or [] for team in rosters}

    return league_rosters, id_to_name, id_to_pos, id_to_team

@st.cache_data
def load_rankings(path="FantasyPros_2025_Dynasty_ALL_Rankings.csv"):
    df = pd.read_csv(path)
    return {row["PLAYER NAME"]: row["RK"] for _, row in df.iterrows()}

# --- Simulation and draft logic ---
def simulate_and_draft(rosters, id_to_name, id_to_pos, max_protect, pos_caps, num_teams, picks_per_team, draft_format, protection_overrides):
    breakdown, pool = {}, []

    for owner, roster_ids in rosters.items():
        if not roster_ids:
            continue
        protected = protection_overrides.get(owner, roster_ids[:max_protect])[:max_protect]
        candidates = [pid for pid in roster_ids if pid not in protected]

        losses, by_pos = [], {}
        for pid in candidates:
            pos = id_to_pos.get(pid, "UNK")
            by_pos.setdefault(pos, []).append(pid)
        for pos, pids in by_pos.items():
            cap = pos_caps.get(pos, len(pids))
            losses.extend(pids[:cap])

        breakdown[owner] = {
            "protected": [id_to_name[p] for p in protected],
            "losses": [id_to_name[p] for p in losses]
        }
        pool.extend(losses)

    total_picks = num_teams * picks_per_team
    draft_pool_ids = pool[:total_picks]

    teams = [f"Expansion Team {i+1}" for i in range(num_teams)]
    picks = {t: [] for t in teams}
    for idx, pid in enumerate(draft_pool_ids):
        rnd = idx // num_teams
        order = teams if rnd % 2 == 0 else list(reversed(teams)) if draft_format == "Snake" else teams
        team = order[idx % num_teams]
        picks[team].append(pid)

    return breakdown, draft_pool_ids, picks

# --- AI functions ---
def ai_protect(roster_ids, id_to_name, id_to_pos, id_to_rank, max_protect, pos_caps, client):
    if not roster_ids:
        return []
    roster_list = [{"name": id_to_name[p], "position": id_to_pos[p], "rank": id_to_rank.get(id_to_name[p], 9999)} for p in roster_ids]
    prompt = (
        "You're a fantasy football GM. Protect exactly " + str(max_protect) + " players based on dynasty rankings (lower rank is better), position scarcity, and long-term potential. "
        f"Roster: {json.dumps(roster_list)}. Max positional losses: {json.dumps(pos_caps)}. Respond with a JSON array of names."
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"system", "content":"Fantasy roster protection."}, {"role":"user", "content":prompt}],
            temperature=0.2
        )
        names = json.loads(resp.choices[0].message.content)
    except Exception:
        names = []
    name_to_id = {v: k for k, v in id_to_name.items()}
    return [name_to_id[n] for n in names if n in name_to_id][:max_protect]

# --- Streamlit UI ---
league_id = st.text_input("🔢 Enter your Sleeper League ID", value="1186327865394335744")

if league_id:
    rosters, id_to_name, id_to_pos, id_to_team = load_league_data(league_id)
    id_to_rank = load_rankings()

    with st.expander("⚙️ Settings", expanded=True):
        max_protect = st.slider("Max Protected per Team", 0, 20, 12)
        num_teams = st.number_input("Expansion Teams", 1, 4, 2)
        picks_per_team = st.number_input("Picks per Expansion Team", 1, 50, 25)
        draft_format = st.radio("Draft Format", ["Snake", "Linear"], index=0)

        rb_cap = st.number_input("RB cap", 0, 10, 2)
        wr_cap = st.number_input("WR cap", 0, 10, 3)
        te_cap = st.number_input("TE cap", 0, 10, 1)
        qb_cap = st.number_input("QB cap", 0, 5, 1)
        flex_cap = st.number_input("Other positions cap", 0, 10, 5)
        pos_caps = {"RB": rb_cap, "WR": wr_cap, "TE": te_cap, "QB": qb_cap, "UNK": flex_cap}

        use_ai = st.checkbox("🤖 Use AI for protections & draft")
        run = st.button("▶️ Run Simulation & Draft")

    if run:
        if use_ai:
            client = OpenAI(api_key=st.secrets["openai"]["api_key"])
            final_protected = {}
            for owner, roster_ids in rosters.items():
                if roster_ids:
                    final_protected[owner] = ai_protect(roster_ids, id_to_name, id_to_pos, id_to_rank, max_protect, pos_caps, client)
        else:
            final_protected = {owner: roster_ids[:max_protect] for owner, roster_ids in rosters.items()}

        breakdown, pool_ids, picks_by_team = simulate_and_draft(
            rosters, id_to_name, id_to_pos, max_protect, pos_caps,
            num_teams, picks_per_team, draft_format, final_protected
        )

        tab1, tab2, tab3 = st.tabs(["Team Breakdown", "Draft Pool", "Expansion Rosters"])
        with tab1:
            st.dataframe(pd.DataFrame.from_dict(breakdown, orient="index"), use_container_width=True)
        with tab2:
            st.write([id_to_name[p] for p in pool_ids])
        with tab3:
            for team, pids in picks_by_team.items():
                st.subheader(team)
                st.write([id_to_name[p] for p in pids])

