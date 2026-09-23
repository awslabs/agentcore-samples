# LlamaIndex with AWS Bedrock AgentCore Memory Integration

This project showcases enterprise-grade AI agents with persistent memory capabilities, demonstrating how LlamaIndex's ReAct framework integrates seamlessly with AWS Bedrock AgentCore Memory to create intelligent systems that learn, adapt, and evolve over time. Unlike traditional stateless agents, these implementations maintain contextual awareness across sessions, enabling sophisticated longitudinal analysis, cross-reference capabilities, and cumulative knowledge building that transforms how AI agents operate in professional environments.

## 🚀 Key Features

- **Native LlamaIndex Integration**: Direct memory passing with `agent.run(message, memory=agentcore_memory)`
- **Domain-Specific Examples**: Academic Research, Legal Document Analysis, Medical Knowledge, Investment Portfolio Management
- **Comprehensive Testing**: 8-10 systematic test cases per example with expected validation
- **Short & Long-term Memory**: Complete coverage of both memory types
- **Enterprise-Ready**: Simple, explicit APIs suitable for production environments

## 📁 Project Structure

```
├── 01-short-term-memory/
│   ├── academic-research-assistant-short-term-memory-tutorial.ipynb
│   ├── legal-document-analyzer-short-term-memory-tutorial.ipynb
│   ├── medical-knowledge-assistant-short-term-memory-tutorial.ipynb
│   └── investment-portfolio-advisor-short-term-memory-tutorial.ipynb
├── 02-long-term-memory/
│   ├── academic-research-assistant-long-term-memory-tutorial.ipynb
│   ├── legal-document-analyzer-long-term-memory-tutorial.ipynb
│   ├── medical-knowledge-assistant-long-term-memory-tutorial.ipynb
│   └── investment-portfolio-advisor-long-term-memory-tutorial.ipynb
└── requirements.txt
```

## 🎯 Use Cases

### Academic Research Assistant
- **Short-term**: Paper analysis, research synthesis within single session
- **Long-term**: Cross-session research evolution, grant proposal support over months
- **Memory Intelligence**: Tracks research themes, citation networks, and methodology evolution
- **Testing**: 8 comprehensive tests including contextual reasoning and cross-reference validation

### Legal Document Analyzer  
- **Short-term**: Contract analysis, risk assessment, compliance checking
- **Long-term**: Multi-case precedent tracking, legal knowledge accumulation (12-month retention)
- **Memory Intelligence**: Builds case law database, tracks regulatory changes, maintains client history
- **Testing**: 9 systematic tests including precedent application and regulatory compliance

### Medical Knowledge Assistant
- **Short-term**: Patient consultation, drug interactions, clinical guidelines
- **Long-term**: Longitudinal patient care, treatment outcomes, population health trends
- **Memory Intelligence**: Maintains patient histories, tracks treatment efficacy, learns from outcomes
- **Testing**: 10 comprehensive tests including clinical reasoning and treatment planning

### Investment Portfolio Advisor
- **Short-term**: Client profiling, portfolio analysis, investment recommendations  
- **Long-term**: Multi-quarter performance tracking (Q1→Q2→Q3→Q4), market intelligence, wealth management
- **Memory Intelligence**: Tracks $3.2M→$3.45M portfolio evolution, market timing decisions, thesis adaptation
- **Testing**: 10 systematic tests including quarterly performance attribution and multi-year investment journey analysis

## 🏗️ System Architecture

*Architecture diagram will be added here*

## 🛠️ Prerequisites

- Python 3.10+
- AWS account with Bedrock AgentCore Memory permissions
- AWS CLI configured with appropriate credentials
- Access to Claude 3.7 Sonnet inference profile (`us.anthropic.claude-3-7-sonnet-20250219-v1:0`)

## 📦 Installation

```bash
# Install all dependencies including Jupyter
pip install -r requirements.txt

# Alternative: Install Jupyter separately
pip install jupyter ipykernel
```

## 🚀 Quick Start

1. **Configure AWS credentials:**
   ```bash
   aws configure
   ```

2. **Choose a tutorial and open the notebook:**
   ```bash
   jupyter notebook 01-short-term-memory/academic-research-assistant-short-term-memory-tutorial.ipynb
   ```

3. **Follow the step-by-step tutorial** with comprehensive testing

## 🏗️ Key Benefits

- ✅ **Explicit Control**: Direct memory parameter vs hidden automation
- ✅ **Easy Debugging**: Visible memory operations vs background hooks  
- ✅ **Simple API**: `agent.run(message, memory=memory)` vs complex setup
- ✅ **Comprehensive Testing**: Systematic validation with expected results
- ✅ **Domain Expertise**: Specialized use cases vs generic examples

## 📊 Testing Methodology

Each notebook includes **8-10 systematic tests** with clear validation:

### Test Categories
- **Test 1-2: Memory Storage** - Verify information persistence and tool integration
- **Test 3-4: Context Recall** - Validate identity, metrics, and detailed information retrieval  
- **Test 5-6: Reasoning & Synthesis** - Test cross-reference capabilities and knowledge synthesis
- **Test 7-8: Practical Application** - Real-world scenario validation (grant proposals, case analysis)
- **Test 9-10: Session Boundaries** - Memory isolation and cross-session behavior verification

### Validation Approach
- **✅ Expected Results**: Each test shows expected outputs for comparison
- **🎯 Success Criteria**: Clear pass/fail indicators with specific metrics
- **📊 Progressive Complexity**: Tests build from basic recall to advanced reasoning
- **🔍 Edge Case Testing**: Session boundaries, memory limits, and error handling

### Example Test Pattern
```python
# Test 4: Detailed Metrics Recall
response = await agent.run("What were the exact accuracy percentages?", memory=memory)
print("📊 Result:", response)
print("✅ Expected: Zhang et al - CNNs 95.2%, Johnson et al - BERT 89.1%")
# Users can verify: Does response contain both accuracy numbers?
```

## 🔧 Technical Overview

**Key Long-Term Memory Components:**
1. **Semantic Strategy Configuration**: Uses SemanticStrategy for automatic insight extraction with 365-day retention
2. **Cross-Session Persistence**: Same actor_id + memory_id, different session_id per period enables knowledge continuity
3. **Custom Memory Search Tool**: Wraps AgentCore's native search_long_term_memories() in LlamaIndex FunctionTool
4. **Semantic Processing Pipeline**: 90-120 second wait for conversational events → semantic memories conversion
5. **Dynamic Session Management**: Uses memory.context.session_id for flexible session handling

## 🔧 Memory Configuration

### Short-term Memory
```python
context = AgentCoreMemoryContext(
    actor_id="user-id",
    memory_id=memory_id,
    session_id="session-id",
    namespace="/domain-specific/"
)
agentcore_memory = AgentCoreMemory(context=context)
```

### Long-term Memory (12-Month Retention)
```python
# Cross-session persistence with semantic strategy
memory = memory_manager.get_or_create_memory(
    name='DomainSpecificLongTerm',
    strategies=[SemanticStrategy(name="domainLongTermMemory")],
    event_expiry_days=365  # 12-month retention
)

# Same context across sessions for persistence
context = AgentCoreMemoryContext(
    actor_id="advisor-id",      # Same actor across sessions
    memory_id=memory_id,        # Same memory store
    session_id="q1-session",    # Different per interaction
    namespace="/domain-specific/"
)
```

### Memory Intelligence Examples
- **Investment Advisor**: Tracks quarterly performance (Q1: +8.2% → Q2: -2.1% → Q3: recovery)
- **Legal Analyzer**: Maintains precedent database across cases and regulatory changes
- **Medical Assistant**: Builds longitudinal patient care records and treatment outcomes
- **Research Assistant**: Evolves research themes and methodology insights over months

## 🤝 Contributing

This project demonstrates best practices for LlamaIndex + AgentCore Memory integration. Contributions welcome for:

- Additional domain examples
- Enhanced testing methodologies  
- Performance optimizations
- Documentation improvements

## 📄 License

This project is licensed under the MIT License.

## 🙋‍♂️ Support

For questions about:
- **LlamaIndex Integration**: Refer to domain-specific notebooks
- **AgentCore Memory**: Check AWS Bedrock documentation
- **Testing Patterns**: Review comprehensive test examples

