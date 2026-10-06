-- Реєстр донорів (варіант 20). Накочується автоматично при першому старті бази.

CREATE TABLE IF NOT EXISTS donor_registry (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    donor_code    TEXT     NOT NULL UNIQUE,
    birth_year    SMALLINT NOT NULL CHECK (birth_year BETWEEN 1900 AND 2100),
    blood_group   TEXT     NOT NULL CHECK (blood_group IN
                      ('O+', 'O-', 'A+', 'A-', 'B+', 'B-', 'AB+', 'AB-')),
    hla_typing    TEXT[]   NOT NULL CHECK (cardinality(hla_typing) >= 1),
    registered_on DATE     NOT NULL,
    available     BOOLEAN  NOT NULL
);

-- Під фільтр і порядок у переліку: WHERE available = $1 ORDER BY id
CREATE INDEX IF NOT EXISTS idx_donor_registry_available_id
    ON donor_registry (available, id);
