#!/usr/bin/env python3

import streamlit as st
import requests
import json
import pandas as pd

# --- Data loading ---
@st.cache_data
def load_league_rosters(league_id):
    rosters = requests.get(f"https://api.sleeper.app/v1/league/{league_id}/rosters").json()
    players = requests.get("https://api.sleeper.app/v1/players/nfl").json()

    id_to_name = {}
    id_to_pos = {}
    for pid, pdata in players.items():
        name = pdata.get("full_name") or f"{pdata.get('first_name','')} {pdata.get('last_name','')}.strip()"
        id_to_name[pid] = name
        id_to_pos[pid] = pdata.get("position", "UNK")

    league_rosters = {team.get("owner_id"): team.get("players") or [] for team in rosters}
    return league_rosters, id_to_name, id_to_pos

# --- Expansion & draft logic ---
def simulate_and_draft(rosters, id_to_name, id_to_pos,
                       max_protect, pos_caps, num_teams, picks_per_team,
                       draft_format, protection_overrides):
    breakdown = {}
    pool = []

    for owner, roster_ids in rosters.items():
        protected = protection_overrides.get(owner, roster_ids[:max_protect])[:max_protect]
        candidates = [pid for pid in roster_ids if pid not in protected]

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

    total_picks = num_teams * picks_per_team
    draft_pool_ids = pool[:total_picks]
    draft_pool_names = [id_to_name[p] for p in draft_pool_ids]

    # Execute the draft order
    teams = [f"Expansion Team {i+1}" for i in range(num_teams)]
    picks_by_team_ids = {team: [] for team in teams}
    for idx, pid in enumerate(draft_pool_ids):
        if draft_format == "Snake":
            rnd = idx // num_teams
            order = teams if rnd % 2 == 0 else list(reversed(teams))
            team = order[idx % num_teams]
        else:
            team = teams[idx % num_teams]
        picks_by_team_ids[team].append(pid)

    return breakdown, draft_pool_names, picks_by_team_ids

# --- Streamlit UI ---
st.set_page_config(layout="wide")
st.title("Dynasty League Expansion Draft Simulator")

league_id = st.text_input("Sleeper League ID", value="1186327865394335744")
if league_id:
    rosters, id_to_name, id_to_pos = load_league_rosters(league_id)

    # Sidebar controls
    st.sidebar.header("Draft Settings")
    max_protect = st.sidebar.slider("Max Protected per Team", 0, 20, 12)
    num_teams = st.sidebar.number_input("Expansion Teams", 1, 4, 2)
    picks_per_team = st.sidebar.number_input("Picks per Expansion Team", 1, 50, 25)
    draft_format = st.sidebar.radio("Draft Format", ["Snake", "Linear"], index=0)

    st.sidebar.subheader("Position Loss Caps")
    rb_cap = st.sidebar.number_input("RB cap", 0, 10, 2)
    wr_cap = st.sidebar.number_input("WR cap", 0, 10, 3)
    te_cap = st.sidebar.number_input("TE cap", 0, 10, 1)
    qb_cap = st.sidebar.number_input("QB cap", 0, 5, 1)
    flex_cap = st.sidebar.number_input("Other positions cap", 0, 10, 5)
    pos_caps = {"RB": rb_cap, "WR": wr_cap, "TE": te_cap, "QB": qb_cap}
    for pos in set(id_to_pos.values()):
        if pos not in pos_caps:
            pos_caps[pos] = flex_cap

    # Protection Overrides
    st.sidebar.subheader("Protection Overrides")
    protection_overrides = {}
    for owner, roster_ids in rosters.items():
        protection_overrides[owner] = st.sidebar.multiselect(
            f"Protected for {owner}", roster_ids,
            default=roster_ids[:max_protect],
            format_func=lambda pid: id_to_name.get(pid, pid)
        )

    # Run simulation & draft
    if st.sidebar.button("Run Simulation & Draft"):
        breakdown, draft_pool, picks_by_team_ids = simulate_and_draft(
            rosters, id_to_name, id_to_pos,
            max_protect, pos_caps, num_teams, picks_per_team,
            draft_format, protection_overrides
        )

        tab1, tab2, tab3, tab4 = st.tabs(
            ["Team Breakdown", "Draft Pool", "Draft Results", "Expansion Rosters"]
        )

        with tab1:
            df1 = pd.DataFrame([
                {"Owner": o,
                 "Protected Count": len(d["protected"]),
                 "Loss Count": len(d["losses"]),
                 "Protected": ", ".join(d["protected"]),
                 "Losses": ", ".join(d["losses"])}
                for o, d in breakdown.items()
            ])
            st.dataframe(df1, use_container_width=True)
            st.download_button("Download Breakdown CSV", df1.to_csv(index=False), "breakdown.csv")

        with tab2:
            df2 = pd.DataFrame({"Player": draft_pool})
            st.dataframe(df2, use_container_width=True)
            st.download_button("Download Pool CSV", df2.to_csv(index=False), "pool.csv")

        with tab3:
            rows = []
            pick_num = 1
            for team, pids in picks_by_team_ids.items():
                for pid in pids:
                    rows.append({"Pick": pick_num, "Team": team, "Player": id_to_name.get(pid, pid)})
                    pick_num += 1
            df3 = pd.DataFrame(rows)
            st.dataframe(df3, use_container_width=True)
            st.download_button("Download Draft Results CSV", df3.to_csv(index=False), "results.csv")

        with tab4:
            for team, pids in picks_by_team_ids.items():
                st.subheader(team)
                rows = [{"Player": id_to_name.get(pid, pid), "Position": id_to_pos.get(pid, "UNK")} for pid in pids]
                df4 = pd.DataFrame(rows)
                st.table(df4)
                st.download_button(f"Download {team} Roster CSV", df4.to_csv(index=False), f"{team}_roster.csv")

        st.success("Simulation and draft complete! Change settings to rerun.")

