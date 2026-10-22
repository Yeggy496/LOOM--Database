-- Derived index, deliberately excluded from the portable MySQL seed.
CREATE VIRTUAL TABLE sample_search USING fts5(
    sample_id UNINDEXED,title,body,summary,tags,tokenize='trigram'
);
INSERT INTO sample_search(sample_id,title,body,summary,tags)
SELECT s.id,s.title,s.body,a.summary_zh,
    coalesce((SELECT group_concat(tag_name,' ') FROM annotation_tags WHERE annotation_id=a.id),'')
FROM samples s JOIN current_annotations a ON a.sample_id=s.id;

CREATE TRIGGER annotations_search AFTER INSERT ON annotation_revisions BEGIN
    DELETE FROM sample_search WHERE sample_id=NEW.sample_id;
    INSERT INTO sample_search(sample_id,title,body,summary,tags)
    SELECT s.id,s.title,s.body,a.summary_zh,
        coalesce((SELECT group_concat(tag_name,' ') FROM annotation_tags WHERE annotation_id=a.id),'')
    FROM samples s JOIN current_annotations a ON a.sample_id=s.id WHERE s.id=NEW.sample_id;
END;
CREATE TRIGGER tags_search AFTER INSERT ON annotation_tags BEGIN
    UPDATE sample_search SET tags=coalesce((
        SELECT group_concat(t.tag_name,' ') FROM annotation_tags t
        JOIN current_annotations a ON a.id=t.annotation_id WHERE a.sample_id=sample_search.sample_id
    ),'') WHERE sample_id=(SELECT sample_id FROM annotation_revisions WHERE id=NEW.annotation_id);
END;
