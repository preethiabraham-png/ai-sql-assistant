
import streamlit as st
import sqlite3
import os
import re
import time

from dotenv import load_dotenv
from google import genai
from google.genai import errors


# --------------------------------------------------
# 1. Configuration
# --------------------------------------------------

st.set_page_config(
    page_title="AI SQL Assistant",
    layout="wide"
)

load_dotenv()

gemini_key = os.getenv("GEMINI_API_KEY")

if not gemini_key:
    st.error(
        "GEMINI_API_KEY not found. "
        "Please add it to your .env file."
    )
    st.stop()

client = genai.Client(api_key=gemini_key)

# Use a model available to your Gemini API key.
MODEL_NAME = "gemini-3.8-flash"


# --------------------------------------------------
# 2. App title and introduction
# --------------------------------------------------

st.title("AI SQL Assistant")

st.write(
    "Ask a question in plain English, and I will "
    "generate a SQL query to retrieve information from "
    "the database."
)


# --------------------------------------------------
# 3. Available database tables
# --------------------------------------------------

st.subheader("Available Data")

st.write(
    "This database contains three tables. Explore the "
    "tables and their columns below to see what you can ask."
)

with st.expander("1. Customers", expanded=False):

    st.write("Information about customers.")

    st.code(
        """customers(
    CustomerID,
    Name,
    City,
    Email
)""",
        language="sql"
    )


with st.expander("2. Products", expanded=False):

    st.write("Information about products.")

    st.code(
        """products(
    ProductID,
    ProductName,
    Category,
    Price
)""",
        language="sql"
    )


with st.expander("3. Orders", expanded=False):

    st.write("Information about customer orders.")

    st.code(
        """orders(
    OrderID,
    CustomerID,
    ProductID,
    OrderDate,
    Quantity,
    Total
)""",
        language="sql"
    )

st.caption(
    "Table relationships: orders.CustomerID connects to "
    "customers.CustomerID, and orders.ProductID connects "
    "to products.ProductID."
)


# --------------------------------------------------
# 4. Example questions
# --------------------------------------------------

st.subheader("Not sure what to ask?")

st.markdown(
    """
    - Which cities have the most customers?
    - What are the five most expensive products?
    - Which product categories generate the most revenue?
    - Which customers have placed the most orders?
    - What is the total revenue by product?
    """
)

st.divider()


# --------------------------------------------------
# 5. Generate SQL using Gemini
# --------------------------------------------------

def generate_sql_from_question(question):

    schema = """
    TABLE customers(CustomerID, Name, City, Email);
    TABLE products(ProductID, ProductName, Category, Price);
    TABLE orders(OrderID, CustomerID, ProductID, OrderDate, Quantity, Total).
    """

    prompt = f"""
    You convert natural-language questions into SQLite queries.

    Database schema:
    {schema}

    Rules:
    - Return exactly one SQL query and nothing else.
    - Do not include explanations, markdown fences, or comments.
    - The query must start with SELECT or WITH.
    - Use only the tables and columns listed above.
    - Use SQLite-compatible syntax.
    - Never generate modifying statements.
    - Join tables using CustomerID and ProductID where appropriate.
    - For revenue, use orders.Total unless the question asks
      for a different calculation.
    - Do not assume columns or tables that are not listed.

    User question:
    {question}
    """

    # Retry temporary 503 server overload errors.
    for attempt in range(3):

        try:
            response = client.models.generate_content(
                model=MODEL_NAME,
                contents=prompt,
                config={
                    "temperature": 0.1
                }
            )

            sql_query = (response.text or "").strip()

            # Remove optional markdown code fences.
            sql_query = re.sub(
                r"^```(?:sql)?\s*",
                "",
                sql_query,
                flags=re.IGNORECASE
            )

            sql_query = re.sub(
                r"\s*```$",
                "",
                sql_query
            ).strip()

            return sql_query

        except errors.APIError as e:

            if e.code != 503 or attempt == 2:
                raise

            # Wait 2 seconds, then 4 seconds.
            time.sleep(2 ** (attempt + 1))

    raise RuntimeError(
        "Gemini could not generate SQL after retries."
    )


# --------------------------------------------------
# 6. SQL safety validation
# --------------------------------------------------

def is_safe_sql(query):

    query = query.strip()

    # Permit only SELECT or WITH statements.
    if not re.match(
        r"^(SELECT|WITH)\b",
        query,
        re.IGNORECASE
    ):
        return False

    # Reject multiple SQL statements.
    query_without_trailing_semicolon = query.rstrip().rstrip(";")

    if ";" in query_without_trailing_semicolon:
        return False

    return True


# --------------------------------------------------
# 7. Run SQL safely against SQLite
# --------------------------------------------------

def run_sql_query(query):

    connection = None

    try:
        # Open the existing database in read-only mode.
        connection = sqlite3.connect(
            "file:sample_database.db?mode=ro",
            uri=True
        )

        # Prevent access to tables outside the known schema.
        allowed_tables = {
            "customers",
            "products",
            "orders"
        }

        allowed_columns = {
            "customers": {
                "customerid", "name", "city", "email"
            },
            "products": {
                "productid", "productname", "category", "price"
            },
            "orders": {
                "orderid", "customerid", "productid",
                "orderdate", "quantity", "total"
            }
        }

        def authorize(action, arg1, arg2, db_name, trigger_name):

            if action in (
                sqlite3.SQLITE_SELECT,
                sqlite3.SQLITE_FUNCTION
            ):
                return sqlite3.SQLITE_OK

            if action == sqlite3.SQLITE_READ:
                table = (arg1 or "").lower()
                column = (arg2 or "").lower()

                if table not in allowed_tables:
                    return sqlite3.SQLITE_DENY

                if column and column not in allowed_columns[table]:
                    return sqlite3.SQLITE_DENY

                return sqlite3.SQLITE_OK

            return sqlite3.SQLITE_DENY

        connection.set_authorizer(authorize)

        cursor = connection.cursor()
        cursor.execute(query)

        results = cursor.fetchall()

        if cursor.description is None:
            return None, "The query did not return a result set."

        columns = [
            description[0]
            for description in cursor.description
        ]

        return results, columns

    except sqlite3.Error as e:
        return None, str(e)

    finally:
        if connection:
            connection.close()


# --------------------------------------------------
# 8. User question form
# --------------------------------------------------

st.subheader("Ask a Question")

with st.form("user_question_form"):

    user_question = st.text_input(
        "What would you like to know about your data?",
        placeholder="e.g. Which five products have the highest sales?"
    )

    submitted = st.form_submit_button("Generate SQL")


# --------------------------------------------------
# 9. Generate SQL and display results
# --------------------------------------------------

if submitted:

    if not user_question.strip():
        st.warning("Please enter a question first.")
        st.stop()

    try:
        # Generate SQL.
        with st.spinner("Generating your SQL query..."):

            sql = generate_sql_from_question(user_question)

        st.subheader("Generated SQL")
        st.code(sql, language="sql")

        # Validate SQL.
        if not is_safe_sql(sql):

            st.error(
                "The generated query failed the safety checks. "
                "Please try rephrasing your question."
            )

            st.stop()

        # Execute the query.
        with st.spinner("Querying the database..."):

            results, columns_or_error = run_sql_query(sql)

        st.subheader("Results")

        # Display results or errors.
        if results is None:

            st.error(
                f"Error running SQL: {columns_or_error}"
            )

        elif not results:

            st.info("No results found for your question.")

        else:

            st.dataframe(
                [
                    dict(zip(columns_or_error, row))
                    for row in results
                ],
                use_container_width=True
            )

            st.caption(
                f"Returned {len(results):,} rows."
            )

    except errors.APIError as e:

        if e.code == 503:

            st.error(
                "Gemini is temporarily overloaded. "
                "Please wait and try again. If the problem "
                "continues, try another model available "
                "in Google AI Studio."
            )

        elif e.code == 429:

            st.error(
                "The Gemini API rate limit or quota may "
                "have been reached. Check your usage and "
                "limits in Google AI Studio."
            )

        else:

            st.error(
                f"Gemini API error ({e.code}): {e}"
            )

    except Exception as e:

        st.error(
            f"Something went wrong: {e}"
        )
