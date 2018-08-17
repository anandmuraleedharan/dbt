{% macro snowflake__create_table_as(temporary, relation, sql) -%}
  {% if temporary %}
    use schema {{ adapter.quote_as_configured(schema, 'schema') }};
  {% endif %}

  {{ default__create_table_as(temporary, relation, sql) }}
{% endmacro %}