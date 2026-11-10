"""Swiss Parliament Monitor: Streamlit in Snowflake dashboard.

Reads only the MART views (05_marts.sql) and the Cortex eval views (04).
Deployed with snowflake/07_streamlit.sql.
"""
import altair as alt
import pandas as pd
import streamlit as st
from snowflake.snowpark.context import get_active_session

st.set_page_config(page_title="Swiss Parliament Monitor", layout="wide")
session = get_active_session()


@st.cache_data(ttl=600)
def q(sql: str) -> pd.DataFrame:
    return session.sql(sql).to_pandas()


st.title("Swiss Parliament Monitor")
st.caption(
    "Curia Vista open data → Alteryx (extract, clean, data quality) → Snowflake (MERGE, stream, task) "
    "→ Cortex AI (translate, summarise, classify, filter)"
)

k = q("SELECT * FROM PARLIAMENT.MART.V_KPIS").iloc[0]
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Items since 2025", f"{int(k.ITEMS_TOTAL):,}")
c2.metric("Last 30 days", f"{int(k.ITEMS_LAST_30D):,}")
c3.metric("Enriched by Cortex", f"{int(k.ITEMS_ENRICHED):,}")
c4.metric("AI / digital items", f"{int(k.ITEMS_DIGITAL or 0):,}")
c5.metric("DQ checks failing", int(k.DQ_CHECKS_FAILING or 0),
          help=f"Last Alteryx run: {k.LAST_ALTERYX_RUN}")

tab_watch, tab_topics, tab_party, tab_eval, tab_ops = st.tabs(
    ["AI / digital watchlist", "Topics over time", "Parties x topics", "Cortex evaluation", "Pipeline health"]
)

with tab_watch:
    st.subheader("Items Cortex flagged as AI, data, digitalisation or cyber")
    w = q("""SELECT SUBMITTED_ON, SHORT_NUMBER, BUSINESS_TYPE_NAME AS TYPE, TITLE_EN, SUMMARY_EN,
                    PARTY, CANTON, STATUS, SOURCE_URL
               FROM PARLIAMENT.MART.V_WATCHLIST ORDER BY SUBMITTED_ON DESC""")
    st.dataframe(
        w, use_container_width=True, hide_index=True,
        column_config={"SOURCE_URL": st.column_config.LinkColumn("Link", display_text="open")},
    )

with tab_topics:
    tm = q("SELECT MONTH, TOPIC_EN, ITEMS FROM PARLIAMENT.MART.V_TOPIC_MONTHLY")
    top = tm.groupby("TOPIC_EN")["ITEMS"].sum().sort_values(ascending=False)
    pick = st.multiselect("Topics", list(top.index), default=list(top.index[:6]))
    chart = (
        alt.Chart(tm[tm.TOPIC_EN.isin(pick)])
        .mark_line(point=True)
        .encode(x=alt.X("MONTH:T", title=None), y=alt.Y("ITEMS:Q", title="Items submitted"),
                color=alt.Color("TOPIC_EN:N", title="Topic"), tooltip=["MONTH:T", "TOPIC_EN", "ITEMS"])
        .properties(height=380)
    )
    st.altair_chart(chart, use_container_width=True)

with tab_party:
    pt = q("SELECT PARTY, TOPIC_EN, ITEMS, SHARE_OF_PARTY FROM PARLIAMENT.MART.V_PARTY_TOPIC")
    big = pt.groupby("PARTY")["ITEMS"].sum().sort_values(ascending=False).index[:7]
    heat = (
        alt.Chart(pt[pt.PARTY.isin(big)])
        .mark_rect()
        .encode(x=alt.X("PARTY:N", sort=list(big), title=None),
                y=alt.Y("TOPIC_EN:N", title=None, sort="-color"),
                color=alt.Color("SHARE_OF_PARTY:Q", title="Share of party's items", scale=alt.Scale(scheme="blues")),
                tooltip=["PARTY", "TOPIC_EN", "ITEMS", alt.Tooltip("SHARE_OF_PARTY:Q", format=".1%")])
        .properties(height=620)
    )
    st.altair_chart(heat, use_container_width=True)
    st.caption("Lead author's party. Committee and cantonal initiatives are excluded.")

with tab_eval:
    st.subheader("Does AI_CLASSIFY reproduce the official topic tags?")
    m = q("SELECT * FROM PARLIAMENT.AI.V_EVAL_METRICS")
    if m.empty:
        st.info("Run snowflake/04_cortex_eval.sql first.")
    else:
        st.dataframe(m, use_container_width=True, hide_index=True)
        best = m.iloc[0].VARIANT
        tf = q(f"""SELECT TOPIC, OFFICIAL_COUNT, PREDICTED_COUNT, F1
                     FROM PARLIAMENT.AI.V_EVAL_TOPIC_F1 WHERE VARIANT = '{best}' ORDER BY F1 DESC""")
        st.markdown(f"Per-topic F1 for the best variant, `{best}`")
        st.altair_chart(
            alt.Chart(tf).mark_bar().encode(
                x=alt.X("F1:Q", scale=alt.Scale(domain=[0, 1])),
                y=alt.Y("TOPIC:N", sort="-x", title=None),
                tooltip=["TOPIC", "OFFICIAL_COUNT", "PREDICTED_COUNT", "F1"],
            ).properties(height=560),
            use_container_width=True,
        )

with tab_ops:
    st.subheader("Data-quality checks (latest run per check)")
    st.dataframe(q("""SELECT ENTITY, CHECK_NAME, EXPECTED, ACTUAL, FAILED_ROWS, STATUS, DETAIL, INSERTED_AT
                        FROM PARLIAMENT.MART.V_DQ_LATEST ORDER BY ENTITY, CHECK_NAME"""),
                 use_container_width=True, hide_index=True)
    st.subheader("Last load per table")
    st.dataframe(q("""SELECT TARGET_TABLE, RUN_ID, ROWS_LANDED, ROWS_INSERTED, ROWS_UPDATED, ROWS_DELETED, LOGGED_AT
                        FROM PARLIAMENT.MART.V_LOAD_LATEST ORDER BY TARGET_TABLE"""),
                 use_container_width=True, hide_index=True)
    st.subheader("Rejected rows (last 200)")
    st.dataframe(q("""SELECT RUN_ID, ENTITY, RECORD_KEY, REASON, INSERTED_AT
                        FROM PARLIAMENT.OPS.DQ_REJECTS ORDER BY INSERTED_AT DESC LIMIT 200"""),
                 use_container_width=True, hide_index=True)
