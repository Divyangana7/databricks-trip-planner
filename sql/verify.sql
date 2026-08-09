-- ============================================================================
-- verify.sql — run after schema.sql + seed.sql.
-- Each query is a piece of grading evidence: screenshot the result of the
-- "minimums" block for your submission (it proves counts + distinct values).
-- ============================================================================

-- 1) Row counts vs. inferred minimums (all should PASS) ----------------------
SELECT 'users'            AS table_name, COUNT(*) AS rows, 2  AS minimum, (COUNT(*) >= 2)  AS pass FROM users
UNION ALL SELECT 'destinations',      COUNT(*), 3,  (COUNT(*) >= 3)  FROM destinations
UNION ALL SELECT 'trips',             COUNT(*), 3,  (COUNT(*) >= 3)  FROM trips
UNION ALL SELECT 'activities',        COUNT(*), 12, (COUNT(*) >= 12) FROM activities
UNION ALL SELECT 'itinerary_items',   COUNT(*), 9,  (COUNT(*) >= 9)  FROM itinerary_items
UNION ALL SELECT 'weather_snapshots', COUNT(*), 6,  (COUNT(*) >= 6)  FROM weather_snapshots
UNION ALL SELECT 'packing_items',     COUNT(*), 6,  (COUNT(*) >= 6)  FROM packing_items
ORDER BY table_name;

-- 2) Distinct-value checks ---------------------------------------------------
SELECT COUNT(DISTINCT country)  AS distinct_countries FROM destinations;   -- expect >= 2
SELECT COUNT(DISTINCT category) AS distinct_activity_categories FROM activities; -- expect >= 4
SELECT
    COUNT(*) FILTER (WHERE is_outdoor)     AS outdoor_activities,
    COUNT(*) FILTER (WHERE NOT is_outdoor) AS indoor_activities
FROM activities;                                                            -- expect both > 0

-- 3) Relationship integrity (every child resolves to a parent) ---------------
SELECT
    (SELECT COUNT(*) FROM trips t            LEFT JOIN users u ON u.user_id = t.user_id WHERE u.user_id IS NULL)               AS orphan_trips,
    (SELECT COUNT(*) FROM itinerary_items i  LEFT JOIN trips t ON t.trip_id = i.trip_id WHERE t.trip_id IS NULL)              AS orphan_itinerary,
    (SELECT COUNT(*) FROM packing_items p    LEFT JOIN trips t ON t.trip_id = p.trip_id WHERE t.trip_id IS NULL)             AS orphan_packing,
    (SELECT COUNT(*) FROM weather_snapshots w LEFT JOIN destinations d ON d.destination_id = w.destination_id WHERE d.destination_id IS NULL) AS orphan_weather;
    -- all four should be 0

-- 4) Multi-day itinerary proof (the Banff trip should have 3 days x 3 items) --
SELECT tr.title, i.day_date, COUNT(*) AS items_that_day
FROM itinerary_items i
JOIN trips tr ON tr.trip_id = i.trip_id
WHERE tr.title = 'Rockies Long Weekend'
GROUP BY tr.title, i.day_date
ORDER BY i.day_date;

-- 5) Embedding coverage (0% before embed_job.py, 100% after) -----------------
SELECT 'destinations.description_embedding' AS col,
       COUNT(*) FILTER (WHERE description_embedding IS NOT NULL) AS filled,
       COUNT(*) AS total FROM destinations
UNION ALL
SELECT 'activities.requirements_embedding',
       COUNT(*) FILTER (WHERE requirements_embedding IS NOT NULL),
       COUNT(*) FROM activities
UNION ALL
SELECT 'users.notes_embedding',
       COUNT(*) FILTER (WHERE notes_embedding IS NOT NULL),
       COUNT(*) FROM users;
