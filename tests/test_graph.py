import unittest

from controller.graph import Topology
from topology import layout


def diamond():
    topo = Topology()
    for s in layout.SWITCHES:
        topo.add_switch(s, layout.switch_ports(s))
    for a, pa, b, pb in layout.LINKS:
        topo.add_link(a, pa, b, pb)
        topo.add_link(b, pb, a, pa)
    return topo


class GraphTests(unittest.TestCase):
    def test_shortest_path_across_diamond(self):
        topo = diamond()
        self.assertEqual(topo.shortest_path(1, 4), [1, 2, 4])
        self.assertEqual(topo.out_ports([1, 2, 4]), [4, 2])

    def test_path_changes_when_link_removed(self):
        topo = diamond()
        topo.remove_link(2, 2)
        topo.remove_link(4, 4)
        self.assertEqual(topo.shortest_path(1, 4), [1, 3, 4])

    def test_no_path_when_partitioned(self):
        topo = diamond()
        for a, pa, b, pb in [(2, 2, 4, 4), (3, 2, 4, 5)]:
            topo.remove_link(a, pa)
            topo.remove_link(b, pb)
        self.assertIsNone(topo.shortest_path(1, 4))

    def test_half_known_link_is_ignored(self):
        topo = Topology()
        topo.add_switch(1, [1, 2])
        topo.add_switch(2, [1])
        topo.add_link(1, 2, 2, 1)
        self.assertEqual(topo.neighbors(1), {})
        topo.add_link(2, 1, 1, 2)
        self.assertEqual(topo.neighbors(1), {2: 2})

    def test_tree_has_every_switch_once_and_no_cycle(self):
        parent, in_port, children = diamond().tree(4)
        self.assertEqual(sorted(parent), [1, 2, 3, 4])
        edges = sum(len(c) for c in children.values())
        self.assertEqual(edges, 3)

    def test_host_ports_exclude_links_even_after_link_removed(self):
        topo = diamond()
        self.assertEqual(topo.host_ports(1), [1, 2, 3])
        topo.remove_link(1, 4)
        self.assertEqual(topo.host_ports(1), [1, 2, 3])
        self.assertTrue(topo.is_inter_switch_port(1, 4))

    def test_reserved_ports_are_not_host_ports(self):
        topo = Topology()
        topo.add_switch(1, [1, 0xfffffffe])
        self.assertEqual(topo.host_ports(1), [1])


if __name__ == "__main__":
    unittest.main()
