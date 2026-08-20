const { Client } = require('pg');
const fs = require('fs');
const path = require('path');

const connectionString = 'postgres://acexam:b561fdb8df2224750b632724bc942381@100.70.92.5:5432/acexam_staging';

async function migrate() {
    const client = new Client({ connectionString });
    try {
        await client.connect();
        console.log('Connected to Staging DB.');

        const schema1 = fs.readFileSync(path.join(__dirname, 'schema.sql'), 'utf8');
        console.log('Executing Phase 1 schema...');
        await client.query(schema1);
        
        const schema2 = fs.readFileSync(path.join(__dirname, 'schema_phase2.sql'), 'utf8');
        console.log('Executing Phase 2 schema...');
        await client.query(schema2);

        console.log('Migrations complete!');
    } catch (err) {
        console.error('Migration failed:', err);
    } finally {
        await client.end();
    }
}

migrate();
