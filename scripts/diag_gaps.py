import duckdb
con = duckdb.connect(":memory:")

print("=== 清洗后数据：段内相邻点时间间隔分布 ===")
rows = con.execute("""
WITH ordered AS (
    SELECT 
        clean_segment_id, mmsi, timestamp,
        lon, lat,
        lead(timestamp) OVER (PARTITION BY clean_segment_id ORDER BY timestamp) AS next_ts,
        lead(lon) OVER (PARTITION BY clean_segment_id ORDER BY timestamp) AS next_lon,
        lead(lat) OVER (PARTITION BY clean_segment_id ORDER BY timestamp) AS next_lat
    FROM read_parquet('data/processed/ais_points_cleaned.parquet')
)
SELECT
    count(*) AS total_intervals,
    sum(CASE WHEN (next_ts - timestamp) > 300 THEN 1 ELSE 0 END) AS gap_5min,
    sum(CASE WHEN (next_ts - timestamp) > 600 THEN 1 ELSE 0 END) AS gap_10min,
    sum(CASE WHEN (next_ts - timestamp) > 3600 THEN 1 ELSE 0 END) AS gap_1hr,
    max(next_ts - timestamp) AS max_gap_sec,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY (next_ts - timestamp)) AS median_gap_sec,
    percentile_cont(0.99) WITHIN GROUP (ORDER BY (next_ts - timestamp)) AS p99_gap_sec
FROM ordered
WHERE next_ts IS NOT NULL
""").fetchone()
print(f"总相邻点对数: {rows[0]:,}")
print(f">5分钟 间隔数: {rows[1]:,}")
print(f">10分钟 间隔数: {rows[2]:,}")
print(f">1小时 间隔数: {rows[3]:,}")
print(f"最大间隔(秒): {rows[4]:,}  = {rows[4]/3600:.1f} 小时")
print(f"中位间隔(秒): {rows[5]:.1f}")
print(f"99%分位间隔(秒): {rows[6]:.1f}  = {rows[6]/60:.1f} 分钟")

print()
print("=== 清洗后数据：段内最大空间跳跃（前20）===")
rows2 = con.execute("""
WITH ordered AS (
    SELECT 
        clean_segment_id, mmsi, timestamp, lon, lat,
        lead(lon) OVER (PARTITION BY clean_segment_id ORDER BY timestamp) AS next_lon,
        lead(lat) OVER (PARTITION BY clean_segment_id ORDER BY timestamp) AS next_lat,
        lead(timestamp) OVER (PARTITION BY clean_segment_id ORDER BY timestamp) AS next_ts
    FROM read_parquet('data/processed/ais_points_cleaned.parquet')
)
SELECT
    clean_segment_id, mmsi,
    round(lon,4) AS lon, round(lat,4) AS lat,
    round(next_lon,4) AS next_lon, round(next_lat,4) AS next_lat,
    round(sqrt(pow(next_lon-lon,2)+pow(next_lat-lat,2)), 4) AS dist_deg,
    (next_ts - timestamp) AS gap_sec
FROM ordered
WHERE next_ts IS NOT NULL
ORDER BY dist_deg DESC
LIMIT 20
""").fetchall()
for r in rows2:
    print(r)
