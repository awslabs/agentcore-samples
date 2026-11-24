# Restored parent infrastructure

The 15 modules under `lib/` were recovered from **awslabs/agentcore-samples**,
commit [`639165a94d14359f820505072696acf17b5a739f`](https://github.com/awslabs/agentcore-samples/tree/639165a94d14359f820505072696acf17b5a739f/01-tutorials/02-AgentCore-gateway/16-vpc-egress/lib),
original directory `01-tutorials/02-AgentCore-gateway/16-vpc-egress/lib/`.
The importing `bin/vpcegress.ts` was byte-identical to that revision. These are
the original implementations, not inferred replacements. The feature-directory
copy had omitted `lib/`; the deleted workshop has **not** been recreated.

Original contributions: Eashan Kaushik, PRs #1247, #1261 and #1415. The public
contributor fork at PR head `fb96cb0158c9901aa3dc4424635f15570a1ca512` returns
the same 15 Git blobs. Recovery verified each Git blob SHA-1 and SHA-256 before
copying. Existing project licensing applies.

Restored modules:

- `vpcegress-stack.ts`
- `vpc-peering-stack.ts`
- `test1-mcp-ecs-stack.ts`, `test2-mcp-eks-stack.ts`, `test3-api-eks-stack.ts`
- `test4-private-apigw-stack.ts`, `test5-private-api-public-cert-stack.ts`
- `test6-public-dns-private-cert-stack.ts`, `test7-private-dns-private-cert-stack.ts`
- `private-domain-stack.ts`, `private-cert-backend-stack.ts`
- `shared/agentcore-gateway-stack.ts`, `shared/eks-cluster-stack.ts`
- `shared/private-ca-stack.ts`, `shared/short-lived-ca-stack.ts`

The parent `package-lock.json` comes from the same revision (original SHA-256
`358504d17c8acf3459d7dc3bb93f5492539c265761193212e5f13845ec630834`).
The current parent lock updates CDK and drops the unused Jest toolchain and the alpha Gateway construct. Both projects use Node's built-in test runner. The parent no longer synthesizes the duplicate `SharedAgentCoreGateway`; its recovered source is available at the historical revision above. The nested CLI CDK lock was generated from
its declared 1.0.3 manifest, not copied from an incompatible installed graph.
Registry URLs and integrity hashes are recorded in both locks.

## Intentional changes after restoration

- The VPC pre-delete custom resource is now a **read-only guard**. It never
  deletes other stacks' security groups, paginates, and uses CDK Provider return
  semantics instead of `cfnresponse`.
- The certificate custom resource preserves certificate IDs on updates, surfaces
  cleanup errors, scopes PCA/SSM permissions, and constrains certificate deletion
  by ownership tag. Its backend `/health` is unauthenticated for ALB health
  checks; item routes retain API-key checks.
- The legacy test-7 backend also exposes `/health` for the ALB while retaining
  item-route API-key checks. The shared EKS stack now exports its actual cluster
  name for consumers; no cluster/resource naming is changed.
- Other restored modules retain their original infrastructure. Their historical
  IAM/retention limitations are documented in `vpcegress/README.md`; restoration
  and synthesis are not a cloud or security certification.

## Companion contracts and packaging

`vpcegress/scripts/proxy_backend.py` and `static_ip.py` implement the existing
SDK proxy and NAT-reuse steps from that same revision's
`03-advanced-concepts/02-private-certificate-authority.ipynb` and
`04-static-gateway-ip.ipynb`, with bounded waits and ownership records added.
The original self-managed notebooks used
`selfManagedLatticeResource.resourceConfigurationIdentifier`; Resource Gateway
and Resource Configuration were **SDK-owned**, not CDK outputs.
`configure_eks_endpoint.py` supplies the same revision's `05-eks-deployment/`
notebooks' DNS-alias/SG wiring, but takes an exact NLB ARN, uses CREATE rather
than UPSERT, and journals owned changes instead of adopting a first name-match.

The two new Dockerfiles package the **current existing** `server.py` files;
neither asset context had a Dockerfile in the recovered history. They do not
introduce a new application. `docker/requirements.in` selects FastMCP 2.14.7;
both generated requirements files pin the full PyPI graph with SHA-256 hashes.
The Docker base is the official Python **3.12.16 slim Bookworm Linux AMD64**
manifest `sha256:0f086103b026048cc50cb5da25ec435c023c5241a83fc44daea593377abc61c4`.
Its OCI source annotation identifies `docker-library/python` revision
`3d7e95597e56a415d9e4abc0f15877e005d2cb2e`, `3.12/slim-bookworm`.
Native server tests, Linux ARM64 ZIP packaging and AMD64 image startup are
independent checks; none establishes private-cloud connectivity.

The initial host-specific hash lock omitted Linux-only keyring dependencies.
`docker/lock_requirements.py` now uses uv 0.9.28 universal resolution, constrained
to every existing pin. Published metadata for keyring 25.7.0 requires
`SecretStorage>=3.2` and `jeepney>=0.4.2` on Linux; the resolved pins are 3.5.0 and
0.9.0. Four Windows-conditional packages are also retained by universal
resolution. No existing package version (including FastMCP 2.14.7) was changed.
Both contexts contain identical complete hash locks with platform markers. PyPI
artifact hashes are in those files; installation still requires hashes and
binary wheels rather than running source builds.
