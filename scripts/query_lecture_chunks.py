#!/usr/bin/env python3
"""Query lecture chunks from the vector knowledge table.

Usage:
    python scripts/query_lecture_chunks.py <lecture_id>
    python scripts/query_lecture_chunks.py eb7f5e3c-e42e-4ec9-8c26-5116d014517f
    python scripts/query_lecture_chunks.py eb7f5e3c-e42e-4ec9-8c26-5116d014517f -o output.json
"""

import argparse
import json

from sqlalchemy import create_engine, text

# Configuration
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/studybuddy"
SCHEMA = "public"
TABLE = "lecture_chunks_knowledge"


def main():
    parser = argparse.ArgumentParser(description="Query lecture chunks from vector table")
    parser.add_argument("lecture_id", help="UUID of the lecture to query")
    parser.add_argument("-o", "--output", help="Output file path (JSON format)")
    args = parser.parse_args()

    engine = create_engine(DATABASE_URL)

    with engine.connect() as conn:
        # Query chunks for the specific lecture_id
        # The metadata is stored as JSONB, so we filter on metadata->>'lecture_id'
        result = conn.execute(
            text(f"""
                SELECT
                    id,
                    name,
                    content,
                    meta_data,
                    created_at
                FROM {SCHEMA}.{TABLE}
                WHERE meta_data->>'lecture_id' = :lecture_id
                ORDER BY (meta_data->>'chunk_index')::int
            """),
            {"lecture_id": args.lecture_id}
        )

        rows = result.fetchall()
        print(f"Found {len(rows)} chunks for lecture {args.lecture_id}\n")

        chunks_data = []
        for row in rows:
            chunk = {
                "id": str(row.id),
                "chunk_index": row.meta_data.get("chunk_index"),
                "start_seconds": row.meta_data.get("start_seconds"),
                "end_seconds": row.meta_data.get("end_seconds"),
                "course_id": row.meta_data.get("course_id"),
                "content": row.content,
                "created_at": str(row.created_at) if row.created_at else None,
            }
            chunks_data.append(chunk)

            # Print to console
            print(f"--- Chunk {chunk['chunk_index']} ---")
            print(f"ID: {chunk['id']}")
            print(f"Start: {chunk['start_seconds']:.1f}s - End: {chunk['end_seconds']:.1f}s")
            print(f"Content:\n{chunk['content']}")
            print("\n" + "=" * 80 + "\n")

        # Write to file if output path specified
        if args.output:
            output_data = {
                "lecture_id": args.lecture_id,
                "chunk_count": len(chunks_data),
                "chunks": chunks_data,
            }
            with open(args.output, "w") as f:
                json.dump(output_data, f, indent=2, ensure_ascii=False)
            print(f"Results written to {args.output}")


if __name__ == "__main__":
    main()
