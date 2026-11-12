"""Read-only environment diagnosis of declared MovieLens inputs and target."""
import duckdb,json,sys
from pathlib import Path
connection=duckdb.connect('/workspace/movie_recomm.duckdb',read_only=True)
columns=connection.execute("SELECT table_schema,table_name,column_name,data_type FROM information_schema.columns ORDER BY table_schema,table_name,ordinal_position").fetchall()
inputs={}
for name in ['movielens_movies','movielens_ratings','all_movie_aliases_iso']:
 count=connection.execute('SELECT COUNT(*) FROM "'+name+'"').fetchone()[0]
 inputs[name]={'rows':count,'columns':[c[2:] for c in columns if c[1]==name]}
connection.close()
gold=duckdb.connect(sys.argv[1],read_only=True)
q=gold.execute('SELECT * FROM user_watched_movies LIMIT 0')
result={'scope':'Read-only diagnostic, not an agent/Qwen trial; fixed gold inspected only for target schema',
        'input_inventory':columns,'declared_project_inputs':inputs,
        'native_target':'user_watched_movies','gold_columns':[r[0] for r in q.description],
        'gold_row_count':gold.execute('SELECT COUNT(*) FROM user_watched_movies').fetchone()[0]}
gold.close()
print(json.dumps(result,indent=2))
