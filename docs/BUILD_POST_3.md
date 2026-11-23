# Build post #3: Proofline goes public (drafts)

Drafts for Hassan to post himself, from his own LinkedIn account (GROW-17). Nothing
here is posted by any tool. Put the links in the first comment: the repository, the
case study (`docs/CASE_STUDY.md`, or its page on hassanmansour.com once it exists),
and the demo video.

Rules these drafts follow: no job title, no employer or client named, and no claim
that a command or a file in this repo doesn't back. The posts carry no quality score
because none exists yet.

## English (about 175 words)

> Proofline is public.
>
> It's an open-source agent system built with LangGraph, and it runs on one rule:
> nothing it writes goes out without passing checks in code and getting my sign-off.
>
> What's built:
> → A content agent that plans, researches and writes, with a critic that sends drafts
> back. Two exits, both decided in code.
> → A supervisor that pauses at an approval gate and survives a restart while it waits.
> → Checks with no model in them: structure, prompt injection, personal data. A failed
> check can add a human approval. It can never remove one.
> → An inbox on my phone where I approve, revise or reject. Approving doesn't post
> anything. I paste it myself.
>
> What isn't measured yet: quality. The 20 golden topics are approved and wired into
> CI, but no real output has been scored against them. So there's no score in this
> post, on purpose.
>
> The case study also covers what failed along the way, including an approval gate
> that broke in a way I didn't see coming.
>
> Repo, case study and demo in the first comment.

## Français (environ 200 mots)

> Proofline est public.
>
> C'est un système d'agents à code source ouvert, bâti avec LangGraph, et il suit une
> seule règle : rien de ce qu'il rédige ne sort sans passer des contrôles écrits en
> code et sans mon approbation.
>
> Ce qui est construit :
> → Un agent de contenu qui planifie, fait la recherche et rédige, avec un critique qui
> renvoie les brouillons. Deux sorties, toutes deux décidées dans le code.
> → Un superviseur qui s'arrête à une porte d'approbation et survit à un redémarrage
> pendant qu'il attend.
> → Des contrôles sans modèle : structure, injection de requêtes, renseignements
> personnels. Un contrôle raté peut ajouter une approbation humaine, jamais en retirer
> une.
> → Une boîte d'approbation sur mon téléphone, où j'approuve, je demande une révision
> ou je refuse. Approuver ne publie rien : je colle le texte moi-même.
>
> Ce qui n'est pas encore mesuré : la qualité. Les 20 sujets du jeu de référence sont
> approuvés et branchés dans l'intégration continue, mais aucun vrai texte n'a encore
> été évalué. Il n'y a donc aucune note dans cette publication, et c'est voulu.
>
> L'étude de cas raconte aussi ce qui a échoué en chemin, dont une porte d'approbation
> qui ne se comportait pas comme prévu.
>
> Dépôt, étude de cas et démo en premier commentaire.

## What each claim rests on

Re-check these on the day you post. If C5 or the golden-set run has landed since, a
line may need to change.

| Claim in the post | Where it comes from | Check |
|---|---|---|
| Open source, LangGraph | `LICENSE` (Apache-2.0), `pyproject.toml` files | the repo page |
| Critic loop with two exits decided in code | `routing.route_after_critic`, ADR-0004 | `uv run proofline mermaid` |
| Approval gate survives a restart | `packages/measured_ops/tests/test_ops_e2e.py` | `uv run pytest packages/measured_ops/tests/test_ops_e2e.py -v` |
| Checks with no model; a failed check only adds approval | `measured_evals/checks.py`, ADR-0018, the row × risk × eval test | `uv run pytest -m "not live" -q` |
| Approve, revise or reject on the phone; approving doesn't post | ADR-0019, ADR-0020, `docs/INBOX.md` | the demo video |
| 20 golden topics approved, wired into CI, nothing scored | `docs/GOLDEN_SET.md`, `.github/workflows/evals.yml` | `uv run python -m measured_evals run` prints `20/20` with a fake writer; `docs/EVALS.md` still says the baseline is a placeholder |
| An approval gate that broke unexpectedly | ADR-0016, "Alternatives considered" | case study, section 4 |

If the golden-set run has produced a real, reviewed baseline by the time you post,
replace the "isn't measured yet" paragraph with the measured number and its source,
not before.
