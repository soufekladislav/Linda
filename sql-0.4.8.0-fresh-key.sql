-- Linda 0.4.8.0 Fresh Key — pouze aditivní změny.
ALTER TABLE users ADD COLUMN IF NOT EXISTS fresh_key_identity_id VARCHAR(36) NULL;
ALTER TABLE projects ADD COLUMN IF NOT EXISTS fresh_key_project_id VARCHAR(36) NULL;
ALTER TABLE social_connectors ADD COLUMN IF NOT EXISTS fresh_key_connection_id VARCHAR(36) NULL;
ALTER TABLE social_connectors ADD COLUMN IF NOT EXISTS fresh_key_managed TINYINT(1) NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS project_members (
  id BIGINT NOT NULL AUTO_INCREMENT,
  project_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  role VARCHAR(40) NOT NULL DEFAULT 'member',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_linda_project_member (project_id,user_id),
  KEY ix_linda_project_member_user (user_id),
  KEY ix_linda_project_member_project (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO project_members(project_id,user_id,role)
SELECT id,user_id,'owner' FROM projects;
