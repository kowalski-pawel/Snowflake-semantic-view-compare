import streamlit as st
import yaml
import re
import copy
from snowflake.snowpark.context import get_active_session


session = get_active_session()


# --- Data Fetching ---

def get_semantic_views_list():
    try:
        df = session.sql("SHOW SEMANTIC VIEWS IN ACCOUNT").collect()
        views = []
        for row in df:
            db = row["database_name"]
            schema = row["schema_name"]
            name = row["name"]
            views.append(f"{db}.{schema}.{name}")
        return views
    except Exception as e:
        st.error(f"Error listing semantic views: {e}")
        return []


def get_semantic_view_yaml(view_name: str) -> dict:
    try:
        result = session.sql(
            f"SELECT SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW('{view_name}')"
        ).collect()
        if result:
            yaml_str = result[0][0]
            return yaml.safe_load(yaml_str)
        return {}
    except Exception as e:
        st.error(f"Error fetching YAML for {view_name}: {e}")
        return {}


# --- Comparison Logic ---

def extract_objects(view_yaml: dict) -> dict:
    """Extract comparable objects grouped by category from parsed YAML."""
    objects = {
        "custom_instructions": [],
        "tables": [],
        "relationships": [],
        "verified_queries": [],
    }

    # Custom instructions
    desc = view_yaml.get("description", "")
    if desc:
        objects["custom_instructions"].append({
            "name": "description",
            "content": desc
        })

    mci = view_yaml.get("module_custom_instructions", {})
    if mci.get("sql_generation"):
        objects["custom_instructions"].append({
            "name": "sql_generation",
            "content": mci["sql_generation"]
        })
    if mci.get("question_categorization"):
        objects["custom_instructions"].append({
            "name": "question_categorization",
            "content": mci["question_categorization"]
        })

    # Tables (each table is one object with all nested elements)
    for table in view_yaml.get("tables", []):
        objects["tables"].append({
            "name": table.get("name", ""),
            "content": table
        })

    # Relationships
    for rel in view_yaml.get("relationships", []):
        objects["relationships"].append({
            "name": rel.get("name", ""),
            "content": rel
        })

    # Verified queries
    for vq in view_yaml.get("verified_queries", []):
        q_name = vq.get("question", vq.get("name", ""))
        objects["verified_queries"].append({
            "name": q_name,
            "content": vq
        })

    return objects


def compare_category(left_objects: list, right_objects: list):
    """Compare objects within one category by name."""
    left_map = {obj["name"].upper().strip(): obj for obj in left_objects}
    right_map = {obj["name"].upper().strip(): obj for obj in right_objects}

    all_names = list(dict.fromkeys(
        [obj["name"].upper().strip() for obj in left_objects] +
        [obj["name"].upper().strip() for obj in right_objects]
    ))

    results = []
    for name in all_names:
        left_obj = left_map.get(name)
        right_obj = right_map.get(name)
        if left_obj and right_obj:
            if left_obj["content"] == right_obj["content"]:
                results.append((left_obj, right_obj, "identical"))
            else:
                results.append((left_obj, right_obj, "different"))
        elif left_obj:
            results.append((left_obj, None, "only_left"))
        else:
            results.append((None, right_obj, "only_right"))
    return results


# --- UI Helpers ---

def get_status_color(status: str) -> str:
    if status == "identical":
        return "#d4edda"
    elif status == "different":
        return "#f8d7da"
    return "#fff3cd"


def escape_html(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def format_yaml_snippet(content, max_lines=15) -> str:
    """Format content as YAML snippet for display."""
    if isinstance(content, str):
        return content[:500]
    try:
        text = yaml.dump(content, default_flow_style=False, allow_unicode=True, sort_keys=False)
        lines = text.split('\n')
        if len(lines) > max_lines:
            text = '\n'.join(lines[:max_lines]) + '\n  ...'
        return text[:800]
    except Exception:
        return str(content)[:500]


def display_comparison_section(comparisons, section_key_prefix, selections):
    """Display comparison rows for one category."""
    for left_obj, right_obj, status in comparisons:
        name = (left_obj or right_obj)["name"]
        key = f"{section_key_prefix}::{name}"
        color = get_status_color(status)

        col1, col2, col3 = st.columns([5, 5, 2])

        with col1:
            if left_obj:
                snippet = escape_html(format_yaml_snippet(left_obj["content"]))
                st.markdown(
                    f'<div style="background-color:{color};padding:8px;border-radius:4px;'
                    f'font-size:11px;white-space:pre-wrap;overflow-x:auto;margin-bottom:4px;">'
                    f'<code>{snippet}</code></div>',
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    '<div style="background-color:#e9ecef;padding:8px;border-radius:4px;'
                    'min-height:40px;color:#6c757d;font-style:italic;margin-bottom:4px;">'
                    '-- empty --</div>', unsafe_allow_html=True)

        with col2:
            if right_obj:
                snippet = escape_html(format_yaml_snippet(right_obj["content"]))
                st.markdown(
                    f'<div style="background-color:{color};padding:8px;border-radius:4px;'
                    f'font-size:11px;white-space:pre-wrap;overflow-x:auto;margin-bottom:4px;">'
                    f'<code>{snippet}</code></div>',
                    unsafe_allow_html=True)
            else:
                st.markdown(
                    '<div style="background-color:#e9ecef;padding:8px;border-radius:4px;'
                    'min-height:40px;color:#6c757d;font-style:italic;margin-bottom:4px;">'
                    '-- empty --</div>', unsafe_allow_html=True)

        with col3:
            if status == "identical":
                st.markdown('<span style="color:green;">identical</span>', unsafe_allow_html=True)
                selections[key] = "left"
            elif status == "only_left":
                ch = st.selectbox("", ["Left (keep)", "Skip"], key=key, label_visibility="collapsed")
                selections[key] = "left" if "Left" in ch else "skip"
            elif status == "only_right":
                ch = st.selectbox("", ["Right (keep)", "Skip"], key=key, label_visibility="collapsed")
                selections[key] = "right" if "Right" in ch else "skip"
            else:
                ch = st.selectbox("", ["Left", "Right"], key=key, label_visibility="collapsed")
                selections[key] = "left" if ch == "Left" else "right"


# --- YAML Generation ---

def generate_merged_yaml(new_name: str, selections: dict,
                         left_objects: dict, right_objects: dict,
                         left_yaml: dict, right_yaml: dict) -> str:
    """Generate merged YAML from selections."""
    merged = {"name": new_name}

    # Build maps for lookup
    left_ci_map = {obj["name"]: obj["content"] for obj in left_objects["custom_instructions"]}
    right_ci_map = {obj["name"]: obj["content"] for obj in right_objects["custom_instructions"]}
    left_table_map = {obj["name"].upper(): obj["content"] for obj in left_objects["tables"]}
    right_table_map = {obj["name"].upper(): obj["content"] for obj in right_objects["tables"]}
    left_rel_map = {obj["name"].upper(): obj["content"] for obj in left_objects["relationships"]}
    right_rel_map = {obj["name"].upper(): obj["content"] for obj in right_objects["relationships"]}
    left_vq_map = {obj["name"].upper().strip(): obj["content"] for obj in left_objects["verified_queries"]}
    right_vq_map = {obj["name"].upper().strip(): obj["content"] for obj in right_objects["verified_queries"]}

    # Process selections
    description = ""
    sql_generation = ""
    question_categorization = ""
    tables = []
    relationships = []
    verified_queries = []

    for key, choice in selections.items():
        if choice == "skip":
            continue
        parts = key.split("::", 1)
        category = parts[0]
        obj_name = parts[1] if len(parts) > 1 else ""

        if category == "custom_instructions":
            source_map = left_ci_map if choice == "left" else right_ci_map
            value = source_map.get(obj_name, "")
            if obj_name == "description":
                description = value
            elif obj_name == "sql_generation":
                sql_generation = value
            elif obj_name == "question_categorization":
                question_categorization = value

        elif category == "table":
            source_map = left_table_map if choice == "left" else right_table_map
            content = source_map.get(obj_name.upper())
            if content:
                tables.append(copy.deepcopy(content))

        elif category == "relationship":
            source_map = left_rel_map if choice == "left" else right_rel_map
            content = source_map.get(obj_name.upper())
            if content:
                relationships.append(copy.deepcopy(content))

        elif category == "verified_query":
            source_map = left_vq_map if choice == "left" else right_vq_map
            content = source_map.get(obj_name.upper().strip())
            if content:
                verified_queries.append(copy.deepcopy(content))

    # Assemble
    if description:
        merged["description"] = description
    if tables:
        merged["tables"] = tables
    if relationships:
        merged["relationships"] = relationships
    if sql_generation or question_categorization:
        mci = {}
        if sql_generation:
            mci["sql_generation"] = sql_generation
        if question_categorization:
            mci["question_categorization"] = question_categorization
        merged["module_custom_instructions"] = mci
    if verified_queries:
        merged["verified_queries"] = verified_queries

    return yaml.dump(merged, default_flow_style=False, allow_unicode=True, sort_keys=False)


# ===== MAIN APP =====

st.set_page_config(page_title="Semantic View Compare (YAML)", layout="wide")
st.title("Semantic View Comparison (YAML)")
st.markdown("Select two semantic views to compare. Uses full YAML representation with time_dimensions, filters, metrics.")

if "views_list" not in st.session_state:
    st.session_state.views_list = get_semantic_views_list()

views = st.session_state.views_list

if not views:
    st.warning("No semantic views found.")
    st.stop()

col_left_sel, col_right_sel = st.columns(2)
with col_left_sel:
    left_choice = st.selectbox("Left semantic view", views, key="left_view_select")
with col_right_sel:
    right_choice = st.selectbox("Right semantic view", views, key="right_view_select",
                                 index=min(1, len(views) - 1))

if st.button("Compare", type="primary"):
    with st.spinner("Fetching YAML..."):
        left_yaml = get_semantic_view_yaml(left_choice)
        right_yaml = get_semantic_view_yaml(right_choice)
    if left_yaml and right_yaml:
        st.session_state.left_yaml = left_yaml
        st.session_state.right_yaml = right_yaml
        st.session_state.left_objects = extract_objects(left_yaml)
        st.session_state.right_objects = extract_objects(right_yaml)
        st.session_state.compared = True
    else:
        st.error("Failed to fetch YAML.")

if st.session_state.get("compared"):
    left_yaml = st.session_state.left_yaml
    right_yaml = st.session_state.right_yaml
    left_objects = st.session_state.left_objects
    right_objects = st.session_state.right_objects
    selections = {}

    # Legend
    st.markdown("---")
    lc1, lc2, lc3 = st.columns(3)
    with lc1:
        st.markdown('<div style="background-color:#d4edda;padding:6px 12px;border-radius:4px;text-align:center;">Identical</div>', unsafe_allow_html=True)
    with lc2:
        st.markdown('<div style="background-color:#f8d7da;padding:6px 12px;border-radius:4px;text-align:center;">Different</div>', unsafe_allow_html=True)
    with lc3:
        st.markdown('<div style="background-color:#fff3cd;padding:6px 12px;border-radius:4px;text-align:center;">Only in one</div>', unsafe_allow_html=True)

    # Header
    st.markdown("---")
    hcol1, hcol2, hcol3 = st.columns([5, 5, 2])
    with hcol1:
        st.markdown(f"**LEFT: {left_yaml.get('name', '')}**")
    with hcol2:
        st.markdown(f"**RIGHT: {right_yaml.get('name', '')}**")
    with hcol3:
        st.markdown("**Choice**")

    # Custom Instructions
    if left_objects["custom_instructions"] or right_objects["custom_instructions"]:
        st.markdown("### Custom Instructions")
        display_comparison_section(
            compare_category(left_objects["custom_instructions"], right_objects["custom_instructions"]),
            "custom_instructions", selections)

    # Tables
    if left_objects["tables"] or right_objects["tables"]:
        st.markdown("### Tables")
        st.caption("Each table includes: dimensions, time_dimensions, facts, metrics, filters, foreign_keys")
        display_comparison_section(
            compare_category(left_objects["tables"], right_objects["tables"]),
            "table", selections)

    # Relationships
    if left_objects["relationships"] or right_objects["relationships"]:
        st.markdown("### Relationships")
        display_comparison_section(
            compare_category(left_objects["relationships"], right_objects["relationships"]),
            "relationship", selections)

    # Verified Queries
    if left_objects["verified_queries"] or right_objects["verified_queries"]:
        st.markdown("### Verified Queries")
        display_comparison_section(
            compare_category(left_objects["verified_queries"], right_objects["verified_queries"]),
            "verified_query", selections)

    # YAML Generation
    st.markdown("---")
    st.markdown("## Generate & Deploy")

    col_name, col_schema = st.columns(2)
    with col_name:
        new_view_name = st.text_input("New Semantic View name", value="MY_MERGED_VIEW")
    with col_schema:
        target_schema = st.text_input("Target schema (DB.SCHEMA)", value="PKOWALSKI5.PUBLIC")

    if st.button("Generate YAML", type="primary"):
        merged_yaml = generate_merged_yaml(
            new_view_name, selections,
            left_objects, right_objects,
            left_yaml, right_yaml
        )
        st.session_state.generated_yaml = merged_yaml

    if st.session_state.get("generated_yaml"):
        st.subheader("Generated YAML")
        st.code(st.session_state.generated_yaml, language="yaml")
