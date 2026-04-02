CREATE CONSTRAINT stable_id_unique IF NOT EXISTS FOR (n:Node) REQUIRE n.stable_id IS UNIQUE;
CREATE INDEX node_name IF NOT EXISTS FOR (n:Node) ON (n.name);
CREATE INDEX node_file IF NOT EXISTS FOR (n:Node) ON (n.file_path);
