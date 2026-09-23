package com.example.agent;

import org.springaicommunity.agentcore.annotation.AgentCoreInvocation;
import org.springframework.ai.chat.client.ChatClient;
import org.springframework.ai.chat.model.ChatModel;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.stereotype.Service;

@SpringBootApplication
public class AgentApplication {

    public static void main(String[] args) {
        SpringApplication.run(AgentApplication.class, args);
    }

    /**
     * Minimal conversational agent hosted on AgentCore Runtime.
     * Receives a user message via the /invoke endpoint and returns the LLM response.
     */
    public record AgentRequest(String message) {}

    @Service
    public static class ConversationalAgent {

        private final ChatClient chatClient;

        ConversationalAgent(ChatModel chatModel) {
            this.chatClient = ChatClient.builder(chatModel)
                    .defaultSystem("You are a helpful assistant. Answer concisely.")
                    .build();
        }

        @AgentCoreInvocation
        public String chat(AgentRequest request) {
            return chatClient.prompt(request.message()).call().content();
        }
    }
}
