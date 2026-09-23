package com.example.agent.service;

import com.embabel.agent.api.annotation.AchievesGoal;
import com.embabel.agent.api.annotation.Action;
import com.embabel.agent.api.annotation.Agent;
import com.embabel.agent.api.common.OperationContext;
import com.embabel.agent.api.common.PromptRunner;
import com.embabel.agent.api.invocation.AgentInvocation;
import com.embabel.agent.core.AgentPlatform;
import com.example.agent.model.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springaicommunity.agentcore.annotation.AgentCoreInvocation;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.ai.tool.ToolCallbackProvider;
import org.springframework.beans.factory.annotation.Qualifier;
import org.springframework.stereotype.Service;

/**
 * Embabel GOAP Fact-Checker Agent on AgentCore Runtime with AgentCore Browser.
 *
 * Pipeline: FactCheckRequest → ParsedClaims → VerifiedClaims → FactCheckReport
 *
 * The GOAP planner auto-chains the three @Action methods based on type availability
 * on the Blackboard. The browser-equipped inner ChatClient navigates real web pages
 * to verify claims.
 */
@Agent(description = "Fact-checks user-submitted claims by parsing, browsing web sources, and summarizing results")
@Service
public class FactCheckAgent {

    private static final Logger log = LoggerFactory.getLogger(FactCheckAgent.class);

    private final AgentPlatform agentPlatform;
    private final ObjectMapper objectMapper;
    private final ChatClient browserChatClient;

    public FactCheckAgent(AgentPlatform agentPlatform,
                          ObjectMapper objectMapper,
                          ChatModel chatModel,
                          @Qualifier("browserToolCallbackProvider") ToolCallbackProvider browserTools) {
        this.agentPlatform = agentPlatform;
        this.objectMapper = objectMapper;
        this.browserChatClient = ChatClient.builder(chatModel)
                .defaultToolCallbacks(browserTools)
                .build();
    }

    /** AgentCore Runtime entry point. */
    @AgentCoreInvocation
    public String check(FactCheckRequest request) {
        log.info("Received fact-check request with {} claims", request.claims().size());
        try {
            FactCheckReport report = AgentInvocation
                    .create(agentPlatform, FactCheckReport.class)
                    .invoke(request);
            return objectMapper.writeValueAsString(report);
        } catch (Exception e) {
            log.error("Fact-check failed: {}", e.getMessage(), e);
            throw new RuntimeException("Fact-check failed: " + e.getMessage(), e);
        }
    }

    // ---- GOAP Actions ----

    @Action(description = "Parse raw user input into individual verifiable claims")
    public ParsedClaims parseClaims(FactCheckRequest request, OperationContext ctx) {
        String prompt = """
                Extract individual verifiable factual claims from the following list.
                Return a JSON object with a "claims" array of strings.
                Each claim should be a single, self-contained factual statement.

                Input claims:
                %s
                """.formatted(String.join("\n- ", request.claims()));

        ParsedClaims parsed = ctx.promptRunner().createObject(prompt, ParsedClaims.class);
        log.info("Parsed {} claims", parsed.claims().size());
        return parsed;
    }

    @Action(description = "Verify each claim by browsing web sources using AgentCore Browser")
    public VerifiedClaims verifyClaims(ParsedClaims parsed) {
        String claimsList = String.join("\n- ", parsed.claims());
        String prompt = """
                Verify each claim below by browsing relevant web sources using the browseUrl tool.
                Browse a maximum of 5 URLs total. For each claim, assess whether the source
                supports, contradicts, or is inconclusive.

                Return ONLY a JSON object with a "results" array where each element has:
                - "claim": the original claim
                - "status": "VERIFIED", "UNVERIFIED", or "CONTRADICTED"
                - "sourceUrl": the URL visited
                - "details": one-sentence explanation

                Claims:
                - %s
                """.formatted(claimsList);

        String raw = browserChatClient.prompt(prompt)
                .options(org.springframework.ai.chat.prompt.ChatOptions.builder().maxTokens(2048).build())
                .call().content();

        try {
            String json = extractJson(raw);
            return objectMapper.readValue(json, VerifiedClaims.class);
        } catch (Exception e) {
            log.warn("Failed to parse verification response, marking all UNVERIFIED: {}", e.getMessage());
            var fallback = parsed.claims().stream()
                    .map(c -> new VerifiedClaims.VerifiedClaim(c, "UNVERIFIED", "", "Parse error"))
                    .toList();
            return new VerifiedClaims(fallback);
        }
    }

    @AchievesGoal(description = "Produce a human-readable fact-check report")
    @Action(description = "Summarize verification results into a final report")
    public FactCheckReport summarize(VerifiedClaims verified, OperationContext ctx) {
        String resultsJson;
        try {
            resultsJson = objectMapper.writeValueAsString(verified.results());
        } catch (Exception e) {
            resultsJson = verified.results().toString();
        }

        String prompt = """
                Summarize the following fact-check results into a clear, concise report.
                Use bullet points. For each claim state the verdict and a brief reason.

                Results:
                %s
                """.formatted(resultsJson);

        String summary = ctx.promptRunner().createObject(prompt, String.class);
        log.info("Generated fact-check report");
        return new FactCheckReport(summary, verified.results());
    }

    private static String extractJson(String raw) {
        if (raw == null || raw.isBlank()) return "{}";
        String t = raw.strip();
        if (t.contains("```")) {
            int s = t.indexOf("```"), cs = t.indexOf('\n', s), e = t.lastIndexOf("```");
            if (cs != -1 && e > cs) t = t.substring(cs + 1, e).strip();
        }
        int s = t.indexOf('{'), e = t.lastIndexOf('}');
        return (s != -1 && e > s) ? t.substring(s, e + 1) : t;
    }
}
