from collections import deque

MAX_PHYSICAL_PORT = 0xffffff00


class Topology:
    def __init__(self):
        self.ports = {}
        self.links = {}
        self.ever_linked = {}

    def add_switch(self, dpid, ports):
        self.ports[dpid] = {p for p in ports if p < MAX_PHYSICAL_PORT}
        self.ever_linked.setdefault(dpid, set())

    def remove_switch(self, dpid):
        self.ports.pop(dpid, None)
        for key in [k for k, v in self.links.items() if k[0] == dpid or v[0] == dpid]:
            del self.links[key]

    def add_link(self, src_dpid, src_port, dst_dpid, dst_port):
        self.links[(src_dpid, src_port)] = (dst_dpid, dst_port)
        self.ever_linked.setdefault(src_dpid, set()).add(src_port)

    def remove_link(self, src_dpid, src_port):
        return self.links.pop((src_dpid, src_port), None) is not None

    def neighbors(self, dpid):
        result = {}
        for (src, port), (dst, peer_port) in sorted(self.links.items()):
            if src != dpid or dst in result:
                continue
            if self.links.get((dst, peer_port)) != (src, port):
                continue
            result[dst] = port
        return result

    def is_inter_switch_port(self, dpid, port):
        return port in self.ever_linked.get(dpid, ())

    def host_ports(self, dpid):
        return sorted(self.ports.get(dpid, set()) - self.ever_linked.get(dpid, set()))

    def peer_port(self, dpid, port):
        link = self.links.get((dpid, port))
        return link[1] if link else None

    def tree(self, root):
        parent = {root: None}
        in_port = {}
        children = {root: []}
        queue = deque([root])
        while queue:
            current = queue.popleft()
            for neighbor, out_port in sorted(self.neighbors(current).items()):
                if neighbor in parent:
                    continue
                parent[neighbor] = current
                children[current].append((neighbor, out_port))
                children[neighbor] = []
                in_port[neighbor] = self.links[(current, out_port)][1]
                queue.append(neighbor)
        return parent, in_port, children

    def shortest_path(self, src, dst):
        if src not in self.ports or dst not in self.ports:
            return None
        if src == dst:
            return [src]
        parent, _, _ = self.tree(src)
        if dst not in parent:
            return None
        path = [dst]
        while path[-1] != src:
            path.append(parent[path[-1]])
        path.reverse()
        return path

    def out_ports(self, path):
        return [self.neighbors(a)[b] for a, b in zip(path, path[1:])]
