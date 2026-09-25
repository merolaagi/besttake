TEMPLATES = [
    {
        "id": "system-design",
        "label": "System design: HLD and LLD",
        "topic": "System design: high-level and low-level design",
        "text": """# Foundations
Scalability basics: vertical scaling, horizontal scaling, stateless services
Latency vs throughput: latency, throughput, bottleneck, percentiles
Networking for system design: DNS, HTTP, TCP, REST API
# Core building blocks
Load balancing: load balancer, round robin, health checks, layer 4, layer 7
Caching: cache, cache invalidation, eviction, LRU, write-through, CDN
Databases SQL vs NoSQL: relational database, NoSQL, schema, transactions
Database replication: replication, leader, follower, read replicas
Database sharding: sharding, partition key, rebalancing, hotspots
Consistent hashing: consistent hashing, hash ring, virtual nodes
CAP theorem and consistency: CAP theorem, consistency, availability, partition tolerance, eventual consistency
Message queues and Kafka: message queue, Kafka, producer, consumer, asynchronous
Rate limiting: rate limiter, token bucket, sliding window
# HLD case studies
Design a URL shortener: URL shortener, hashing, base62, redirect
Design a chat system: WebSocket, chat, message delivery, presence
Design a news feed: news feed, fan-out, timeline, ranking
# Low-level design
SOLID principles: single responsibility, open closed, liskov substitution, dependency inversion
Design patterns: singleton, factory, strategy, observer
UML class diagrams: class diagram, association, composition, inheritance
LLD case study parking lot: parking lot, classes, spot, ticket
LLD case study elevator system: elevator, state, scheduling, request""",
    },
    {
        "id": "ml-foundations",
        "label": "Machine learning foundations",
        "topic": "Machine learning foundations",
        "text": """# Basics
What is machine learning: supervised learning, unsupervised learning, features, labels
Linear regression: linear regression, loss function, least squares
Gradient descent: gradient descent, learning rate, convergence
Logistic regression: logistic regression, sigmoid, classification
# Generalization
Overfitting and regularization: overfitting, underfitting, regularization, bias variance
Train test split and cross validation: validation set, cross validation, data leakage
Evaluation metrics: precision, recall, F1, ROC
# Models
Decision trees and random forests: decision tree, random forest, entropy
Support vector machines: SVM, margin, kernel trick
Neural networks: neuron, activation function, layers
Backpropagation: backpropagation, chain rule, gradients
K-means clustering: k-means, clusters, centroids""",
    },
    {
        "id": "dsa",
        "label": "Data structures and algorithms",
        "topic": "Data structures and algorithms",
        "text": """# Foundations
Big O notation: time complexity, space complexity, big O
Arrays and strings: array, string, two pointers
Hash tables: hash table, hash function, collisions
# Linear structures
Linked lists: linked list, pointer, reversal
Stacks and queues: stack, queue, LIFO, FIFO
# Trees and graphs
Binary trees and traversal: binary tree, inorder, preorder, postorder
Binary search trees: binary search tree, insertion, balanced
Heaps and priority queues: heap, priority queue, heapify
Graph traversal BFS and DFS: graph, BFS, DFS, adjacency list
Shortest paths Dijkstra: Dijkstra, shortest path, weighted graph
# Techniques
Binary search: binary search, sorted array
Recursion and backtracking: recursion, backtracking, base case
Dynamic programming: dynamic programming, memoization, subproblems""",
    },
]


def parse_lessons(text: str, topic: str, limit: int = 30) -> list[dict]:
    """Lines starting with # start a module. Other lines are lessons: 'Title: concept, concept'."""
    modules, current = [], None
    for raw in (text or "").splitlines():
        line = raw.strip().lstrip("-*•").strip()
        if not line:
            continue
        if line.startswith("#"):
            current = {"title": line.lstrip("#").strip() or f"Part {len(modules) + 1}", "lessons": []}
            modules.append(current)
            continue
        if current is None:
            current = {"title": "Lessons", "lessons": []}
            modules.append(current)
        title, _, rest = line.partition(":")
        title = title.strip()[:160]
        concepts = [c.strip() for c in rest.split(",") if c.strip()] or [title]
        current["lessons"].append({
            "title": title,
            "concepts": concepts[:8],
            "queries": [f"{title} explained", f"{title} {topic}".strip()[:120]],
        })
    modules = [m for m in modules if m["lessons"]]
    count, out = 0, []
    for m in modules:
        keep = m["lessons"][: max(0, limit - count)]
        count += len(keep)
        if keep:
            out.append({"title": m["title"], "lessons": keep})
    return out
