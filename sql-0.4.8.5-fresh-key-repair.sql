-- Linda 0.4.8.5 – Fresh Key ACL repair
-- Non-destructive: ensures local project access tables/relationship fields and owner mirror rows.
CREATE TABLE IF NOT EXISTS project_members (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  project_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  role VARCHAR(40) NOT NULL DEFAULT 'editor',
  relationship VARCHAR(24) NOT NULL DEFAULT 'member',
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_linda_project_member(project_id,user_id),
  KEY ix_linda_project_member_user(user_id),
  KEY ix_linda_project_member_project(project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS project_invitations (
  id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
  project_id BIGINT NOT NULL,
  email VARCHAR(190) NOT NULL,
  role VARCHAR(40) NOT NULL DEFAULT 'editor',
  relationship VARCHAR(24) NOT NULL DEFAULT 'member',
  status VARCHAR(24) NOT NULL DEFAULT 'pending',
  invited_by BIGINT NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  UNIQUE KEY uq_linda_project_invite(project_id,email),
  KEY ix_linda_project_invite_email(email,status),
  KEY ix_linda_project_invite_project(project_id,status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

ALTER TABLE project_members ADD COLUMN IF NOT EXISTS relationship VARCHAR(24) NOT NULL DEFAULT 'member';
ALTER TABLE project_invitations ADD COLUMN IF NOT EXISTS relationship VARCHAR(24) NOT NULL DEFAULT 'member';
INSERT IGNORE INTO project_members(project_id,user_id,role)
SELECT id,user_id,'owner' FROM projects;
