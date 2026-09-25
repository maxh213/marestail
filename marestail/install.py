from pathlib import Path

from marestail import _install as impl

__all__ = ["install"]


def install(target: Path, gitignore_generated: bool = False, hard: bool = False) -> None:
    impl.write_tree(target, hard)
    impl.apply_hooks(target)
    impl.extend_gitignore(target / ".gitignore", impl.generated_ignore(gitignore_generated, hard))
    impl.trust_grok_folder(target)
    print(impl.done_message(target, hard))
