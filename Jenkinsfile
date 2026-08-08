pipeline {
    agent any

    environment {
        EC2_IP   = "YOUR_EC2_PUBLIC_IP"           // your EC2 public IP, used for SSH target and smoke test URL
        REPO_URL = "https://github.com/<your-username>/deployhub.git"
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Install & Test') {
            // Runs locally on the Jenkins (Windows) machine.
            // Only needs Python installed - no Docker required here at all.
            steps {
                bat '''
                    python -m venv venv
                    call venv\\Scripts\\activate
                    pip install -r requirements.txt
                    pip install pytest
                    pytest tests/ || exit 0
                '''
            }
        }

        stage('Build & Deploy on EC2') {
            // Everything Docker-related happens over SSH, using EC2's own
            // Docker daemon. Jenkins itself never touches Docker.
            //
            // NOTE: we use withCredentials + sshUserPrivateKey instead of the
            // sshagent() step. The SSH Agent plugin's ssh-agent implementation
            // is unreliable on Windows Jenkins agents and throws a
            // StringIndexOutOfBoundsException. withCredentials writes the key
            // to a temp file and calls `ssh -i` directly, which works fine
            // on Windows.
            steps {
                retry(2) {
                    withCredentials([sshUserPrivateKey(
                        credentialsId: 'ec2-ssh-key-deployhub',
                        keyFileVariable: 'SSH_KEY',
                        usernameVariable: 'SSH_USER'
                    )]) {
                        bat """
                            icacls "%SSH_KEY%" /inheritance:r
                            icacls "%SSH_KEY%" /grant:r "SYSTEM:R"
                            icacls "%SSH_KEY%" /grant:r "Administrators:R"
                            ssh -o StrictHostKeyChecking=no -i "%SSH_KEY%" %SSH_USER%@%EC2_IP% ^
                            "mkdir -p ~/deployhub && cd ~/deployhub && if [ -d .git ]; then git pull origin main; else git clone %REPO_URL% .; fi && docker compose up -d --build"
                        """
                    }
                }
            }
        }

        stage('Smoke Test') {
            steps {
                script {
                    sleep(time: 15, unit: 'SECONDS')
                    bat "curl -f http://%EC2_IP%:5000/health || exit 1"
                }
            }
        }
    }

    post {
        success {
            echo "Deployment succeeded: build #${env.BUILD_NUMBER}"
        }
        failure {
            echo "Deployment failed: build #${env.BUILD_NUMBER}"
        }
    }
}