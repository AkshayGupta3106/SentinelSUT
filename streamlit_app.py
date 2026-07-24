"""
Basic Streamlit interface for the ML/DS Interview Prep RAG Chatbot.

Calls rag.pipeline.run_pipeline() directly -- no need to run the FastAPI
server separately for local testing/demo purposes.

Run:
    streamlit run streamlit_app.py
"""

import streamlit as st

if not hasattr(st, "rerun"):
    st.rerun = st.experimental_rerun

from sentinel.collector.instrument import instrument_sut
instrument_sut()  # must run before run_pipeline is ever called

from rag.pipeline import run_pipeline
from sentinel.dashboard import render_dashboard
from sentinel.metrics.dashboard_view import render_metrics
from sentinel.evaluation.dashboard_view import render_evaluation
from sentinel.evaluation.evaluators import EvaluationEngine
from sentinel.regression.dashboard_view import render_regression
from sentinel.rca.dashboard_view import render_rca
from sentinel.reporting.dashboard_view import render_reports

st.set_page_config(page_title="ML/DS Interview Prep RAG", page_icon="🧠")

st.title("🧠 ML/DS Interview Prep Chatbot")
st.caption("System Under Test for Sentinel AI — ask a question from the knowledge base.")

if "messages" not in st.session_state:
    st.session_state.messages = []

with st.sidebar:
    st.header("Settings")
    k = st.slider("Chunks to retrieve (k)", min_value=1, max_value=10, value=5)
    st.divider()
    st.caption("Knowledge base topics:")
    st.caption("- Bias-variance tradeoff\n- Gradient descent variants\n- Classification metrics\n- L1/L2 regularization\n- Transformer attention")
    if st.button("Clear chat"):
        st.session_state.messages = []
        st.rerun()

tab_chat, tab_dashboard, tab_metrics, tab_eval, tab_regression, tab_rca, tab_reports = st.tabs(
    ["💬 Chat", "📊 Sentinel Dashboard", "📈 Metrics", "🧪 Evaluation", "🚨 Regression", "🔍 RCA", "📄 Reports"]
)

with tab_chat:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg["role"] == "assistant" and msg.get("sources"):
                with st.expander("Retrieval details"):
                    st.write("**Sources used:**", ", ".join(msg["sources"]) or "none")
                    st.write("**Chunks retrieved:**", msg.get("num_chunks_retrieved"))
                    st.write("**Chunks used:**", msg.get("num_chunks_used"))
                    if msg.get("is_fallback"):
                        st.warning("No GEMINI_API_KEY set — this is a fallback response, not a real answer.")

    query = st.chat_input("Ask about bias-variance, regularization, attention...")

    if query:
        st.session_state.messages.append({"role": "user", "content": query})
        with st.chat_message("user"):
            st.markdown(query)

        with st.chat_message("assistant"):
            with st.spinner("Running the RAG pipeline..."):
                try:
                    result = run_pipeline(query, k=k)
                    answer = result["answer"]
                    st.markdown(answer)

                    # Ensure all async trace events are written, sync to DB, and update metrics
                    from sentinel.collector.event_collector import get_collector
                    from sentinel.trace.trace_assembler import sync_events_to_db
                    from sentinel.metrics.metrics_collector import MetricsCollector

                    get_collector().flush()
                    sync_events_to_db()
                    MetricsCollector().compute_and_store()

                    eval_results = EvaluationEngine().evaluate(
                        result["trace_id"], query, result["context"], answer
                    )

                    with st.expander("Retrieval details"):
                        st.write("**Sources used:**", ", ".join(result["sources"]) or "none")
                        st.write("**Chunks retrieved:**", result["num_chunks_retrieved"])
                        st.write("**Chunks used:**", result["num_chunks_used"])
                        st.write("**Trace ID:**", f"`{result['trace_id']}`")
                        if result["is_fallback"]:
                            st.warning("No GEMINI_API_KEY set — this is a fallback response, not a real answer.")
                        if eval_results:
                            st.write("**Evaluation scores:**")
                            for r in eval_results:
                                score_str = f"{r['score']:.2f}" if r["score"] is not None else "skipped"
                                st.write(f"- `{r['evaluator_name']}`: {score_str}")

                    st.session_state.messages.append({
                        "role": "assistant",
                        "content": answer,
                        "sources": result["sources"],
                        "num_chunks_retrieved": result["num_chunks_retrieved"],
                        "num_chunks_used": result["num_chunks_used"],
                        "is_fallback": result["is_fallback"],
                        "trace_id": result["trace_id"],
                    })
                except ValueError as e:
                    st.error(f"Invalid query: {e}")
                except RuntimeError as e:
                    st.error(f"Pipeline error: {e}")

with tab_dashboard:
    try:
        render_dashboard()
    except Exception as e:
        st.error(f"Failed to render dashboard: {e}")
        st.exception(e)

with tab_metrics:
    try:
        render_metrics()
    except Exception as e:
        st.error(f"Failed to render metrics: {e}")
        st.exception(e)

with tab_eval:
    try:
        render_evaluation()
    except Exception as e:
        st.error(f"Failed to render evaluation: {e}")
        st.exception(e)

with tab_regression:
    try:
        render_regression()
    except Exception as e:
        st.error(f"Failed to render regression: {e}")
        st.exception(e)

with tab_rca:
    try:
        render_rca()
    except Exception as e:
        st.error(f"Failed to render RCA: {e}")
        st.exception(e)

with tab_reports:
    try:
        render_reports()
    except Exception as e:
        st.error(f"Failed to render reports: {e}")
        st.exception(e)
