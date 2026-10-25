import manimgx as m


class Scene01(m.Scene):
    """A short vertical clip introducing the chain rule."""

    def construct(self) -> None:
        heading = m.Typst("The *chain rule*", font_size=54)
        formula = m.MathTypst("(dif)/(dif x) f(g(x)) = f'(g(x)) g'(x)", font_size=64)
        hint = m.Typst(
            "differentiate the outside, then the inside",
            font_size=28,
            color=m.BLUE_B,
        )

        formula.scale_to_fit_width(7.2)
        hint.scale_to_fit_width(7.2)
        content = m.VGroup(heading, formula, hint).arrange(m.DOWN, buff=0.8)

        self.play(m.Write(heading), run_time=0.8)
        self.play(m.Write(formula), run_time=1.2)
        self.play(m.FadeIn(hint, shift=m.UP * 0.3), run_time=0.6)
        self.wait(1.0)

        self.play(content.animate.scale(1.05), run_time=0.4)
        self.wait(0.6)
