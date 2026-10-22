-- Fresh-development-database baseline. Target: MySQL 8.0 / 8.4, InnoDB, utf8mb4.
-- Apply once (or through Flyway). Do not execute over application tables.
-- SQLite FTS and its migration ledger are engine-specific and are not copied.
SET NAMES utf8mb4 COLLATE utf8mb4_0900_bin;
CREATE TABLE sources (
    id VARCHAR(64) PRIMARY KEY,
    url VARCHAR(768) NOT NULL UNIQUE,
    author VARCHAR(200) NOT NULL,
    license_status VARCHAR(20) NOT NULL,
    license_name VARCHAR(200),
    license_url VARCHAR(768),
    CHECK(CHAR_LENGTH(TRIM(author))>0),
    CHECK(license_status IN ('unknown','documented')),
    CHECK(license_status='unknown' OR
        (CHAR_LENGTH(TRIM(COALESCE(license_name,'')))>0 AND CHAR_LENGTH(TRIM(COALESCE(license_url,'')))>0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
CREATE TABLE samples (
    id VARCHAR(100) PRIMARY KEY,
    source_id VARCHAR(64),
    origin VARCHAR(20) NOT NULL,
    title VARCHAR(200) NOT NULL,
    language VARCHAR(2) NOT NULL,
    body MEDIUMTEXT NOT NULL,
    body_sha256 VARCHAR(64) NOT NULL,
    locator VARCHAR(500) NOT NULL,
    collected_on VARCHAR(10) NOT NULL,
    group_key VARCHAR(220) NOT NULL,
    split VARCHAR(4) NOT NULL,
    training_allowed INT NOT NULL DEFAULT 0,
    FOREIGN KEY(source_id) REFERENCES sources(id),
    UNIQUE(origin,body_sha256),
    INDEX samples_group(group_key,split),
    CHECK(origin IN ('public_excerpt','synthetic')),
    CHECK(CHAR_LENGTH(TRIM(id)) BETWEEN 1 AND 100),
    CHECK(CHAR_LENGTH(TRIM(title)) BETWEEN 1 AND 200),
    CHECK(language IN ('zh','en')),
    CHECK(CHAR_LENGTH(TRIM(body))>0 AND CHAR_LENGTH(body)<=20000),
    CHECK(CHAR_LENGTH(body_sha256)=64 AND body_sha256 NOT REGEXP '[^0-9a-f]'),
    CHECK(CHAR_LENGTH(TRIM(locator))>0),
    CHECK(CHAR_LENGTH(TRIM(group_key))>0),
    CHECK(split IN ('dev','test')),
    CHECK(training_allowed=0),
    CHECK((origin='public_excerpt' AND source_id IS NOT NULL AND CHAR_LENGTH(body)<=500)
        OR (origin='synthetic' AND source_id IS NULL))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
CREATE TABLE annotation_revisions (
    id VARCHAR(128) PRIMARY KEY,
    sample_id VARCHAR(100) NOT NULL,
    revision INT NOT NULL,
    summary_zh TEXT NOT NULL,
    category VARCHAR(20) NOT NULL,
    origin VARCHAR(20) NOT NULL,
    review_status VARCHAR(20) NOT NULL,
    reviewer VARCHAR(200),
    actor VARCHAR(200) NOT NULL,
    reason TEXT NOT NULL,
    tags_json LONGTEXT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    FOREIGN KEY(sample_id) REFERENCES samples(id),
    UNIQUE(sample_id,revision),
    CHECK(revision>0),
    CHECK(CHAR_LENGTH(TRIM(summary_zh)) BETWEEN 1 AND 4000),
    CHECK(category IN ('knowledge','idea','question','task','event')),
    CHECK(origin IN ('ai_assisted','human')),
    CHECK(review_status IN ('pending','verified','rejected')),
    CHECK(CHAR_LENGTH(TRIM(actor))>0 AND CHAR_LENGTH(TRIM(reason))>0),
    CHECK(JSON_VALID(tags_json) AND JSON_TYPE(tags_json)='ARRAY'),
    CHECK((review_status='pending' AND reviewer IS NULL) OR
        (review_status IN ('verified','rejected') AND CHAR_LENGTH(TRIM(COALESCE(reviewer,'')))>0))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
CREATE TABLE tags (
    name VARCHAR(100) PRIMARY KEY,
    CHECK(CHAR_LENGTH(TRIM(name))>0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
CREATE TABLE annotation_tags (
    annotation_id VARCHAR(128) NOT NULL,
    tag_name VARCHAR(100) NOT NULL,
    PRIMARY KEY(annotation_id,tag_name),
    FOREIGN KEY(annotation_id) REFERENCES annotation_revisions(id),
    FOREIGN KEY(tag_name) REFERENCES tags(name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
CREATE TABLE source_checks (
    id VARCHAR(64) PRIMARY KEY,
    sample_id VARCHAR(100) NOT NULL,
    checked_on VARCHAR(10) NOT NULL,
    method VARCHAR(20) NOT NULL,
    status VARCHAR(20) NOT NULL,
    checker_type VARCHAR(20) NOT NULL,
    evidence TEXT NOT NULL,
    FOREIGN KEY(sample_id) REFERENCES samples(id),
    CHECK(method IN ('web_reader','human_browser')),
    CHECK(status IN ('matched','unavailable','mismatch')),
    CHECK(checker_type IN ('ai_assisted','human')),
    CHECK(CHAR_LENGTH(TRIM(evidence))>0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
CREATE TABLE import_runs (
    id VARCHAR(36) PRIMARY KEY,
    file_name VARCHAR(255) NOT NULL,
    file_sha256 VARCHAR(64) NOT NULL,
    record_count INT NOT NULL,
    inserted_count INT NOT NULL,
    skipped_count INT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    CHECK(record_count>0 AND inserted_count>=0 AND skipped_count>=0),
    CHECK(record_count=inserted_count+skipped_count)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
CREATE TABLE audit_events (
    id VARCHAR(36) PRIMARY KEY,
    sample_id VARCHAR(100),
    action VARCHAR(30) NOT NULL,
    actor VARCHAR(200) NOT NULL,
    details_json LONGTEXT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    FOREIGN KEY(sample_id) REFERENCES samples(id),
    CHECK(action IN ('import','annotation_revision')),
    CHECK(JSON_VALID(details_json))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_bin;
CREATE VIEW current_annotations AS SELECT a.* FROM annotation_revisions a
WHERE a.revision=(SELECT MAX(b.revision) FROM annotation_revisions b WHERE b.sample_id=a.sample_id);

DELIMITER $$
CREATE TRIGGER samples_no_update BEFORE UPDATE ON samples FOR EACH ROW
BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='sample snapshots are immutable'; END$$
CREATE TRIGGER samples_no_delete BEFORE DELETE ON samples FOR EACH ROW
BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='sample snapshots are immutable'; END$$
CREATE TRIGGER sources_no_update BEFORE UPDATE ON sources FOR EACH ROW
BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='source attribution is immutable'; END$$
CREATE TRIGGER sources_no_delete BEFORE DELETE ON sources FOR EACH ROW
BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='source attribution is immutable'; END$$
CREATE TRIGGER annotations_no_update BEFORE UPDATE ON annotation_revisions FOR EACH ROW
BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='append a new annotation revision'; END$$
CREATE TRIGGER annotations_no_delete BEFORE DELETE ON annotation_revisions FOR EACH ROW
BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='annotation history must be preserved'; END$$
CREATE TRIGGER annotation_tags_no_update BEFORE UPDATE ON annotation_tags FOR EACH ROW
BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='annotation tags are immutable'; END$$
CREATE TRIGGER annotation_tags_no_delete BEFORE DELETE ON annotation_tags FOR EACH ROW
BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='annotation tags are immutable'; END$$
CREATE TRIGGER annotation_tags_declared BEFORE INSERT ON annotation_tags FOR EACH ROW
BEGIN
    IF NOT EXISTS(SELECT 1 FROM annotation_revisions a WHERE a.id=NEW.annotation_id
        AND JSON_CONTAINS(a.tags_json,JSON_QUOTE(NEW.tag_name))) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='tag not declared in immutable revision';
    END IF;
END$$
CREATE TRIGGER source_checks_no_update BEFORE UPDATE ON source_checks FOR EACH ROW
BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='source checks are append-only'; END$$
CREATE TRIGGER source_checks_no_delete BEFORE DELETE ON source_checks FOR EACH ROW
BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='source checks are append-only'; END$$
CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events FOR EACH ROW
BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='audit events are append-only'; END$$
CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events FOR EACH ROW
BEGIN SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='audit events are append-only'; END$$
CREATE TRIGGER samples_group_split BEFORE INSERT ON samples FOR EACH ROW
BEGIN
    IF EXISTS(SELECT 1 FROM samples WHERE group_key=NEW.group_key AND split<>NEW.split) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='source group cannot cross dev/test';
    END IF;
END$$
DELIMITER ;
