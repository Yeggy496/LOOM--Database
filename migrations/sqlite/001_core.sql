CREATE TABLE sources (
    id TEXT PRIMARY KEY NOT NULL,
    url TEXT NOT NULL UNIQUE CHECK(length(url) BETWEEN 1 AND 768),
    author TEXT NOT NULL CHECK(length(trim(author)) > 0),
    license_status TEXT NOT NULL CHECK(license_status IN ('unknown','documented')),
    license_name TEXT,
    license_url TEXT,
    CHECK(license_status = 'unknown' OR
        (length(trim(coalesce(license_name,''))) > 0 AND length(trim(coalesce(license_url,''))) > 0))
);

CREATE TABLE samples (
    id TEXT PRIMARY KEY NOT NULL CHECK(length(id) BETWEEN 1 AND 100),
    source_id TEXT REFERENCES sources(id),
    origin TEXT NOT NULL CHECK(origin IN ('public_excerpt','synthetic')),
    title TEXT NOT NULL CHECK(length(trim(title)) BETWEEN 1 AND 200),
    language TEXT NOT NULL CHECK(language IN ('zh','en')),
    body TEXT NOT NULL CHECK(length(trim(body)) > 0 AND length(body) <= 20000),
    body_sha256 TEXT NOT NULL CHECK(length(body_sha256)=64 AND body_sha256 NOT GLOB '*[^0-9a-f]*'),
    locator TEXT NOT NULL CHECK(length(trim(locator)) > 0),
    collected_on TEXT NOT NULL,
    group_key TEXT NOT NULL CHECK(length(trim(group_key)) > 0),
    split TEXT NOT NULL CHECK(split IN ('dev','test')),
    training_allowed INTEGER NOT NULL DEFAULT 0 CHECK(training_allowed=0),
    UNIQUE(origin,body_sha256),
    CHECK((origin='public_excerpt' AND source_id IS NOT NULL AND length(body)<=500)
        OR (origin='synthetic' AND source_id IS NULL))
);
CREATE INDEX samples_group ON samples(group_key,split);
CREATE INDEX samples_source ON samples(source_id);

CREATE TABLE annotation_revisions (
    id TEXT PRIMARY KEY NOT NULL,
    sample_id TEXT NOT NULL REFERENCES samples(id),
    revision INTEGER NOT NULL CHECK(revision>0),
    summary_zh TEXT NOT NULL CHECK(length(trim(summary_zh)) BETWEEN 1 AND 4000),
    category TEXT NOT NULL CHECK(category IN ('knowledge','idea','question','task','event')),
    origin TEXT NOT NULL CHECK(origin IN ('ai_assisted','human')),
    review_status TEXT NOT NULL CHECK(review_status IN ('pending','verified','rejected')),
    reviewer TEXT,
    actor TEXT NOT NULL CHECK(length(trim(actor))>0),
    reason TEXT NOT NULL CHECK(length(trim(reason))>0),
    tags_json TEXT NOT NULL CHECK(json_valid(tags_json) AND json_type(tags_json)='array'),
    created_at TEXT NOT NULL,
    UNIQUE(sample_id,revision),
    CHECK((review_status='pending' AND reviewer IS NULL) OR
        (review_status IN ('verified','rejected') AND length(trim(coalesce(reviewer,'')))>0))
);
CREATE INDEX revisions_latest ON annotation_revisions(sample_id,revision DESC);
CREATE TABLE tags (name TEXT PRIMARY KEY NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 100));
CREATE TABLE annotation_tags (
    annotation_id TEXT NOT NULL REFERENCES annotation_revisions(id),
    tag_name TEXT NOT NULL REFERENCES tags(name),
    PRIMARY KEY(annotation_id,tag_name)
);
CREATE TABLE source_checks (
    id TEXT PRIMARY KEY NOT NULL,
    sample_id TEXT NOT NULL REFERENCES samples(id),
    checked_on TEXT NOT NULL,
    method TEXT NOT NULL CHECK(method IN ('web_reader','human_browser')),
    status TEXT NOT NULL CHECK(status IN ('matched','unavailable','mismatch')),
    checker_type TEXT NOT NULL CHECK(checker_type IN ('ai_assisted','human')),
    evidence TEXT NOT NULL CHECK(length(trim(evidence))>0)
);
CREATE TABLE import_runs (
    id TEXT PRIMARY KEY NOT NULL,
    file_name TEXT NOT NULL,
    file_sha256 TEXT NOT NULL,
    record_count INTEGER NOT NULL CHECK(record_count>0),
    inserted_count INTEGER NOT NULL CHECK(inserted_count>=0),
    skipped_count INTEGER NOT NULL CHECK(skipped_count>=0),
    created_at TEXT NOT NULL,
    CHECK(record_count=inserted_count+skipped_count)
);
CREATE TABLE audit_events (
    id TEXT PRIMARY KEY NOT NULL,
    sample_id TEXT REFERENCES samples(id),
    action TEXT NOT NULL CHECK(action IN ('import','annotation_revision')),
    actor TEXT NOT NULL,
    details_json TEXT NOT NULL CHECK(json_valid(details_json)),
    created_at TEXT NOT NULL
);

CREATE VIEW current_annotations AS
SELECT a.* FROM annotation_revisions a
WHERE a.revision=(SELECT max(b.revision) FROM annotation_revisions b WHERE b.sample_id=a.sample_id);

CREATE TRIGGER samples_no_update BEFORE UPDATE ON samples BEGIN
    SELECT RAISE(ABORT,'sample snapshots are immutable; import a new sample ID'); END;
CREATE TRIGGER samples_no_delete BEFORE DELETE ON samples BEGIN
    SELECT RAISE(ABORT,'sample snapshots are immutable'); END;
CREATE TRIGGER sources_no_update BEFORE UPDATE ON sources BEGIN
    SELECT RAISE(ABORT,'source attribution is immutable; append source-check evidence'); END;
CREATE TRIGGER sources_no_delete BEFORE DELETE ON sources BEGIN
    SELECT RAISE(ABORT,'source attribution is immutable'); END;
CREATE TRIGGER annotations_no_update BEFORE UPDATE ON annotation_revisions BEGIN
    SELECT RAISE(ABORT,'append a new annotation revision'); END;
CREATE TRIGGER annotations_no_delete BEFORE DELETE ON annotation_revisions BEGIN
    SELECT RAISE(ABORT,'annotation history must be preserved'); END;
CREATE TRIGGER annotation_tags_no_update BEFORE UPDATE ON annotation_tags BEGIN
    SELECT RAISE(ABORT,'annotation tags belong to an immutable revision'); END;
CREATE TRIGGER annotation_tags_no_delete BEFORE DELETE ON annotation_tags BEGIN
    SELECT RAISE(ABORT,'annotation tags belong to an immutable revision'); END;
CREATE TRIGGER annotation_tags_declared BEFORE INSERT ON annotation_tags
WHEN NOT EXISTS(SELECT 1 FROM annotation_revisions a,json_each(a.tags_json) j
    WHERE a.id=NEW.annotation_id AND j.value=NEW.tag_name) BEGIN
    SELECT RAISE(ABORT,'tag was not declared in this immutable revision'); END;
CREATE TRIGGER source_checks_no_update BEFORE UPDATE ON source_checks BEGIN
    SELECT RAISE(ABORT,'source checks are append-only'); END;
CREATE TRIGGER source_checks_no_delete BEFORE DELETE ON source_checks BEGIN
    SELECT RAISE(ABORT,'source checks are append-only'); END;
CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events BEGIN
    SELECT RAISE(ABORT,'audit events are append-only'); END;
CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events BEGIN
    SELECT RAISE(ABORT,'audit events are append-only'); END;
CREATE TRIGGER samples_group_split BEFORE INSERT ON samples
WHEN EXISTS(SELECT 1 FROM samples WHERE group_key=NEW.group_key AND split<>NEW.split) BEGIN
    SELECT RAISE(ABORT,'a source group cannot cross dev/test'); END;
