<p align="center">
  <img src="chest.png" alt="" width="160">
</p>

<h1 align="center">Reznar's Arcane Oddities</h1>

> See `SETUP.md` to get the local Postgres running before you start.

**Fork the repo and make incremental pushes. We will follow your commits.**

MulhollandAI has taken on Reznar's Arcane Oddities, a fantasy magic item shop, as a client. Your job is to help Reznar organize their products so that it is easy to add new items for sale and to find patterns across the catalog.

---

## What Reznar can tell you about magic items

"Magic items are strange and mystical things. They come in several grades of rarity, from *common* all the way up to *artifacts*. The most powerful items have to be attuned to their user, and people can only use so many at a time.

A lot of magic items are worn. Rings, amulets, cloaks, gloves, boots, hats, armor, and weapons are all commonly enchanted. Unfortunately, names are not always consistent — something worn on the head might be called a hat, a helmet, or a mask. It's important to distinguish where objects are worn, because people can't use more than one object in each slot.

Reznar is also interested in finding patterns in the magic items, so that he can guide his customers to things that match their needs. He's a little vague on what magic can do, since it can do *anything*, but some dimensions that appear important are offensive improvements, defensive improvements, whether the item is targeted to specific creatures and environments, and whether there are limitations on use."

---

## The assignment

Your source data is in `data/items_combined.pdf`. It is messy.

Design an ontology for Reznar's magic item catalog and populate it from the PDF. There are two deliverables:

### 1. The ontology — `database/schema/*.sql`

Model Reznar's catalog as a declarative Postgres schema, the way our production ontology is built:

- One `.sql` file per object type, each `inherits (object)` (the base class in `foundation.sql`, which gives every entity an `id` and timestamps).
- `create domain` / `create type ... as enum` for your value vocabulary — a rarity, a wear slot, a gold price — so meaning lives in the type and the database rejects anything malformed.
- `comment on table` / `comment on column` describing what each entity and field means. Those comments **are** the ontology's self-description; keep them meaningful.
- Foreign keys for the relationships between entities, so the catalog is traversable.

`sqlc` reads these same files to generate typed Go into `database/generated/` — run `sqlc generate` from `database/` after each schema change. See **`stormland/`** for a complete worked example (a commercial-real-estate lease ontology) built exactly this way, end to end.

Document your design choices: what entity types you created, what fields and value types you defined, and why you structured it the way you did.

### 2. The extraction pipeline — `cmd/extract`

A Go pipeline that reads the PDF and populates the database against your ontology. The source data is imperfect; your pipeline should handle that gracefully. We are an AI-first company and expect the extraction step to be AI-driven, not hand-written parsing. Normalize each record into the canonical vocabulary your domains demand, then insert it through your generated queries — `stormland/example.go` (`Seed`) shows the shape of that write path.

`cmd/extract` is scaffolded: it provisions your schema and opens the PDF. The extraction itself is yours to build.

---

### Note: what even is an ontology?

Ontology is a philosophy term for 'things that exist', and like all philosophy terms there is a lot of *debate* about it. From a software perspective, ontology is the secret sauce that enables Palantir to be Palantir — a set of structured relationships between everything that can be traversed and queried.

- [Palantir docs](https://www.palantir.com/docs/foundry/ontology/overview)
- [Casey Hart YouTube](https://www.youtube.com/watch?v=UW57RW-4kWs&list=PLIHlyoU28t5_gsMf8EkmnQVSHefbR3xqz)

You can also think of it as a database schema. Formally, an ontology is just a bunch of triples, Subject → Predicate → Object, but in practical terms that is a pain to query. There's a lot of pre-existing practice; you may see acronyms like OWL, BFO, and RDF. At Mulholland we move fast, so our ontology is a declarative Postgres schema with typed Go generated from it — the pattern the `stormland/` example demonstrates.

## How this assignment will be evaluated

1. **Ontology design** — the quality of your schema and how well it captures what Reznar described: sensible entity types, value types that encode meaning, honest relationships. There is no single right answer; we want to see your reasoning.

2. **Pipeline quality** — how well your extraction handles the messiness of the source data and how completely it captures the catalog.

3. **Explanation** — we want to follow your thought process throughout. Make incremental commits and consider maintaining a timestamped `log.md` as you work.
