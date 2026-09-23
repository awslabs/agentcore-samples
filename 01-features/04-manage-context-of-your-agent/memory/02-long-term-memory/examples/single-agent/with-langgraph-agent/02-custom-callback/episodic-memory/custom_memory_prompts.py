extraction_prompt = """You are tasked with analyzing conversations to extract meaningful episodic memories from nutrition and meal planning interactions. You'll be analyzing two sets of data:

<past_conversation>
[Past conversations between the user and system will be placed here for context]
</past_conversation>

<current_conversation>
[The current conversation between the user and system will be placed here]
</current_conversation>

Your job is to identify and extract episodic memories that capture complete meal planning sessions, cooking experiences, and nutrition-related interactions. Focus on:

**Episode Structure:**
- **Situation**: What meal planning or nutrition scenario was being addressed?
- **Intent**: What was the user trying to achieve (meal prep, recipe search, dietary goals)?
- **Assessment**: How did the conversation unfold? What decisions were made?
- **Justification**: Why were specific recommendations or choices made?
- **Outcome**: What was the final result or next steps?

**Key Elements to Extract:**
- Complete meal planning sessions with context and decisions
- Recipe discussions including ingredients, substitutions, and modifications
- Dietary goals, restrictions, and preferences expressed during the episode
- Cooking experiences, successes, and challenges
- Temporal context (meal timing, occasions, seasonal preferences)
- Learning moments and skill development in cooking/nutrition

**What NOT to Extract:**
- Single isolated food mentions without context
- Temporary states ("I'm hungry right now")
- Personal information unrelated to nutrition
- Speculative or hypothetical discussions without concrete outcomes

Focus on capturing rich, contextual episodes that would be valuable for future meal planning and nutrition assistance."""

consolidation_prompt = """# ROLE
Episodic Memory Manager for nutrition and meal planning conversations that determines how to handle new episodic memories against existing ones.

# TASK
For each new episodic memory, select exactly ONE operation: AddMemory, UpdateMemory, or SkipMemory.

# OPERATIONS

**AddMemory** - New distinct episode or meal planning session not captured in existing memories
Examples: 
- New meal planning session for a specific occasion
- First-time recipe exploration for a cuisine type
- New dietary goal or challenge being addressed
- Distinct cooking experience or learning moment

**UpdateMemory** - Enhances existing episode with additional context, outcomes, or follow-up
Examples: 
- Follow-up on a previously planned meal with results/feedback
- Additional details about a recipe modification that was tried
- Progress update on a dietary goal from a previous episode
- Continuation of a multi-session meal planning conversation

**SkipMemory** - Not worth storing as an episodic memory
Examples: 
- Incomplete conversations without clear outcomes
- Repetitive information already well-captured
- Single food mentions without episode context
- Temporary preferences or one-off comments
- Personal information without nutrition relevance
- Speculative discussions without concrete decisions

# GUIDELINES
- Prioritize complete episodes with clear situation → intent → outcome flow
- Value temporal context and specific meal planning sessions
- Focus on actionable nutrition and cooking insights
- Avoid fragmentary or incomplete episode captures"""
