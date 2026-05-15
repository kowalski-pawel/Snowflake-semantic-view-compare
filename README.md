# Snowflake Semantic View Compare

Streamlit apps for comparing two Snowflake Semantic Views side-by-side, reviewing differences, and merging selected elements into a new view.

## Overview

The project contains three Streamlit applications:

| File | Approach | Output |
|---|---|---|
| `semantic_view_compare.py` | **Unified** -- fetches both DDL and YAML, uses SQL-based granular comparison | SQL and YAML in tabbed output, with direct execution |
| `semantic_view_compare_sql.py` | DDL only via `GET_DDL('SEMANTIC VIEW', ...)` | `CREATE OR REPLACE SEMANTIC VIEW` SQL |
| `semantic_view_compare_yaml.py` | YAML only via `SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW(...)` | Merged YAML |

**`semantic_view_compare.py`** is the recommended version. It combines both approaches into a single interface, comparing at the SQL/DDL level for granular element-by-element review and generating both SQL and YAML output. The other two files are standalone single-approach alternatives.

### Workflow

1. List all Semantic Views in the account (`SHOW SEMANTIC VIEWS IN ACCOUNT`)
2. Select two views (left / right) and click **Compare**
3. Review differences color-coded by status:
   - **Green** -- identical
   - **Red** -- different
   - **Yellow** -- exists only in one view
4. For each difference choose which side to keep (or skip)
5. Generate output and optionally deploy directly to Snowflake

### Compared elements

- Custom instructions (comment, `ai_sql_generation`, `ai_question_categorization`)
- Tables (with dimensions, time dimensions, facts, metrics, filters, foreign keys)
- Relationships
- Facts, dimensions, metrics (individual granular comparison in unified/SQL versions)
- Verified queries
- Extension data (`WITH EXTENSION (CA=...)` block -- unified/SQL versions)

## Requirements

- Snowflake account with Semantic Views
- Snowflake Snowpark Python session (the apps use `get_active_session()`)
- Python packages: `streamlit`, `snowflake-snowpark-python`, `pyyaml`

## Usage

These apps are designed to run as **Streamlit in Snowflake (SiS)** applications. Upload the chosen file to a Snowflake stage and create a Streamlit app pointing to it.

Alternatively, for local development with an active Snowpark connection:

```bash
pip install streamlit snowflake-snowpark-python pyyaml
streamlit run semantic_view_compare.py
```

## Which version to choose?

- **Unified** (`semantic_view_compare.py`) -- recommended. Fetches both DDL and YAML, provides granular SQL-based comparison (tables, facts, dimensions, metrics, relationships as separate sections), and generates both SQL and YAML output in tabs. Includes an "Execute in Snowflake" button for direct deployment and handles extension data.
- **SQL-only** (`semantic_view_compare_sql.py`) -- standalone DDL-based comparison. Useful if you only need a `CREATE SEMANTIC VIEW` SQL statement.
- **YAML-only** (`semantic_view_compare_yaml.py`) -- standalone YAML-based comparison. Compares tables as whole objects (including time_dimensions, filters, metrics inside each table).

## License

MIT
