# Documentation

Complete documentation for the Personality Proxy API system.

## 📖 Getting Started

Start here if you're new to the project:

- **[Quickstart Guide](guides/quickstart.md)** - Get up and running in 5 minutes

## 📚 Guides

Step-by-step guides for common tasks:

- **[Quickstart](guides/quickstart.md)** - Installation, configuration, and first steps

## 📘 Reference

Technical references and API documentation:

- **[API Reference](reference/api.md)** - Complete API endpoint documentation
- **[AI Providers](reference/providers.md)** - Configure and switch AI backends (Ollama, OpenAI, etc.)
- **[Tools System](reference/tools.md)** - Create custom tools and function calling

## 🧠 Memory System

Documentation for the Neo4j-based memory system:

- **[Memory Overview](memory/overview.md)** - Core concepts and basic usage (V1)
- **[Advanced Features](memory/advanced-features.md)** - Memory hierarchy, decay, consolidation (V2)
- **[Improvements Summary](memory/improvements.md)** - What's new in V2

## 🏗️ Architecture

```
Client (Ollama-compatible) → Personality Proxy API
                                    ├── Provider Layer (Pluggable)
                                    │   ├── Ollama Provider
                                    │   ├── OpenAI Provider
                                    │   └── Custom Providers
                                    ├── Neo4j (Memory DB)
                                    │   ├── Memory Hierarchy
                                    │   ├── Decay & Consolidation
                                    │   └── Analytics
                                    ├── SQLite (Conversation History)
                                    └── Tools System
```

## 🔍 Quick Links

### Common Tasks
- [Install and run the server](guides/quickstart.md#installation)
- [Switch AI providers](reference/providers.md#switching-providers)
- [Configure memory system](memory/overview.md#configuration)
- [Create custom tools](reference/tools.md#creating-tools)
- [Monitor memory health](memory/advanced-features.md#6-memory-analytics)

### API Usage
- [Chat endpoint](reference/api.md#post-apichat)
- [Enable memory](reference/api.md#memory-parameters)
- [Use tools](reference/api.md#tools-usage)

### Memory System
- [Memory types (Episodic, Knowledge, Procedural)](memory/overview.md#memory-types)
- [Memory hierarchy (Working/Short-term/Long-term)](memory/advanced-features.md#1-memory-hierarchy)
- [Background memory writing](memory/advanced-features.md#3-background-memory-writing)
- [Memory consolidation](memory/advanced-features.md#4-memory-consolidation)
- [Memory analytics](memory/advanced-features.md#6-memory-analytics)

## 🤝 Contributing

See the main [README](../README.md#-contributing) for contribution guidelines.

## 🆘 Support

- **Issues**: https://github.com/oOHiyoriOo/nami_ai/issues
- **Discussions**: https://github.com/oOHiyoriOo/nami_ai/discussions

---

**Quick Navigation:** [Main README](../README.md) | [Guides](guides/) | [Reference](reference/) | [Memory System](memory/)
