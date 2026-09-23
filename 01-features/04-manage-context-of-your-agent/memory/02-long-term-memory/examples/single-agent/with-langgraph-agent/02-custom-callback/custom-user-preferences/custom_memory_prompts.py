extraction_prompt = """You are tasked with analyzing conversations to extract the user's culinary and food preferences. You'll be analyzing two sets of data:

<past_conversation>
[Past conversations between the user and system will be placed here for context]
</past_conversation>

<current_conversation>
[The current conversation between the user and system will be placed here]
</current_conversation>

Your job is to identify and categorize the user's food-related preferences into two main types:
- Explicit preferences: Directly stated culinary preferences, dietary restrictions, or food-related choices by the user.
- Implicit preferences: Inferred from patterns in food choices, repeated recipe inquiries, cooking behavior, or contextual clues from their culinary requests.

For explicit preference, extract only food and culinary preferences that the user has explicitly shared. Do not infer the user's preferences.
For implicit preference, it is allowed to infer the user's culinary preferences, but only the ones with strong signals, such as repeatedly asking for specific cuisines, cooking methods, or ingredient preferences."""

consolidation_prompt = """# ROLE
Culinary Memory Manager that determines how to handle new food preferences against existing ones.

# TASK
For each new culinary memory, select exactly ONE operation: AddMemory, UpdateMemory, or SkipMemory.

# OPERATIONS

**AddMemory** - New lasting food preference not in existing memories
Examples: "I'm allergic to shellfish" | "I prefer Mediterranean cuisine" | "I follow keto diet"

**UpdateMemory** - Enhances existing food preference with new details  
Examples: "I love Greek salads and moussaka" (existing: Mediterranean cuisine) | "My shellfish allergy includes crab and lobster" (existing: shellfish allergy)

**SkipMemory** - Not worth storing permanently
Examples: One-time events ("I just ate lunch") | Temporary states ("Craving pizza today") | Redundant info | Personal details without preferences | Speculative assumptions | PII | Harmful dietary content"""
