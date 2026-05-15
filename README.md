# Snowflake Semantic View Compare

Streamlit apps for comparing two Snowflake Semantic Views side-by-side, reviewing differences, and merging selected elements into a new view.

## Overview

The project contains two independent Streamlit applications, each using a different approach to read and compare Semantic Views:

| File | Approach | Output |
|---|---|---|
| `semantic_view_compare.py` | Parses DDL from `GET_DDL('SEMANTIC VIEW', ...)` | Generates `CREATE OR REPLACE SEMANTIC VIEW` SQL |
| `semantic_view_compare_yaml.py` | Parses YAML from `SYSTEM$READ_YAML_FROM_SEMANTIC_VIEW(...)` | Generates merged YAML and deploys via `SYSTEM$CREATE_SEMANTIC_VIEW_FROM_YAML` |

Both apps follow the same workflow:

1. List all Semantic Views in the account (`SHOW SEMANTIC VIEWS IN ACCOUNT`)
2. Select two views (left / right) and click **Compare**
3. Review differences color-coded by status:
   - **Green** -- identical
   - **Red** -- different
   - **Yellow** -- exists only in one view
4. For each difference choose which side to keep (or skip)
5. Generate and optionally deploy the merged result

### Compared elements

- Custom instructions (description, `ai_sql_generation`, `ai_question_categorization`)
- Tables (with dimensions, time dimensions, facts, metrics, filters, foreign keys)
- Relationships
- Verified queries
- Facts, dimensions, metrics (DDL version only)

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
# or
streamlit run semantic_view_compare_yaml.py
```

## Which version to choose?

- **DDL version** (`semantic_view_compare.py`) -- works with the raw SQL definition; useful when you need a `CREATE SEMANTIC VIEW` statement you can version-control or run manually.
- **YAML version** (`semantic_view_compare_yaml.py`) -- works with the full YAML representation including `time_dimensions`, `filters`, and `metrics`; deploys directly via a system procedure.

## License

MIT
