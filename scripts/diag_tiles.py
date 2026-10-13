import duckdb

con = duckdb.connect(":memory:")

print("=== 原始数据：空间跨度最大的轨迹 TOP 20 ===")
rows = con.execute("""
SELECT 
    trajectory_id,
    mmsi,
    count(*) AS pts,
    max(timestamp)-min(timestamp) AS time_span_sec,
    round(max(lon)-min(lon), 3) AS lon_span,
    round(max(lat)-min(lat), 3) AS lat_span,
    round(max(lon)-min(lon) + max(lat)-min(lat), 3) AS total_span
FROM read_parquet('data/processed/ais_points.parquet')
GROUP BY trajectory_id, mmsi
ORDER BY total_span DESC
LIMIT 20
""").fetchall()
for r in rows:
    print(r)

print()
print("=== 原始数据：总览 ===")
row = con.execute("""
SELECT 
    count(*) AS total_pts,
    count(DISTINCT trajectory_id) AS trajectories,
    round(avg(max_lon_span),3) AS avg_lon_span,
    round(max(max_lon_span),3) AS max_lon_span,
    round(avg(max_lat_span),3) AS avg_lat_span,
    round(max(max_lat_span),3) AS max_lat_span
FROM (
    SELECT 
        trajectory_id,
        max(lon)-min(lon) AS max_lon_span,
        max(lat)-min(lat) AS max_lat_span
    FROM read_parquet('data/processed/ais_points.parquet')
    GROUP BY trajectory_id
)
""").fetchone()
print(row)

print()
print("=== 清洗后数据：空间跨度最大的段 TOP 20 ===")
rows2 = con.execute("""
SELECT 
    clean_segment_id,
    mmsi,
    count(*) AS pts,
    max(timestamp)-min(timestamp) AS time_span_sec,
    round(max(lon)-min(lon), 3) AS lon_span,
    round(max(lat)-min(lat), 3) AS lat_span,
    round(max(lon)-min(lon) + max(lat)-min(lat), 3) AS total_span
FROM read_parquet('data/processed/ais_points_cleaned.parquet')
GROUP BY clean_segment_id, mmsi
ORDER BY total_span DESC
LIMIT 20
""").fetchall()
for r in rows2:
    print(r)

print()
print("=== 清洗后数据：总览 ===")
row2 = con.execute("""
SELECT 
    count(*) AS total_pts,
    count(DISTINCT clean_segment_id) AS segments,
    round(avg(max_lon_span),3) AS avg_lon_span,
    round(max(max_lon_span),3) AS max_lon_span,
    round(avg(max_lat_span),3) AS avg_lat_span,
    round(max(max_lat_span),3) AS max_lat_span
FROM (
    SELECT 
        clean_segment_id,
        max(lon)-min(lon) AS max_lon_span,
        max(lat)-min(lat) AS max_lat_span
    FROM read_parquet('data/processed/ais_points_cleaned.parquet')
    GROUP BY clean_segment_id
)
""").fetchone()
print(row2)
